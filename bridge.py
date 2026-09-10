import os
import time
import json
import shutil
import subprocess
import argparse
import atexit
import msvcrt
import tempfile
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

QUEUE_DIR = os.path.join(BASE_DIR, "queue")
WORKING_DIR = os.path.join(BASE_DIR, "working")
RESULT_DIR = os.path.join(BASE_DIR, "result")
COMPLETED_DIR = os.path.join(BASE_DIR, "completed")
FAILED_DIR = os.path.join(BASE_DIR, "failed")
LOG_DIR = os.path.join(BASE_DIR, "logs")
STATUS_FILE = os.path.join(BASE_DIR, "status.json")
STATUS_BACKUP_FILE = os.path.join(BASE_DIR, "status.json.bak")
LOCK_FILE = os.path.join(BASE_DIR, "bridge.lock")
LOG_FILE = os.path.join(LOG_DIR, "bridge.log")
MAX_LOG_BYTES = 5 * 1024 * 1024


def _codex_candidates():
    """Known Codex CLI locations for Windows launchers with a reduced PATH."""
    roots = []
    for value in (os.getenv("LOCALAPPDATA"), os.path.join(os.path.expanduser("~"), "AppData", "Local"), r"C:\\Users\\movie\\AppData\\Local"):
        if value and value not in roots:
            roots.append(value)
    candidates = []
    for root in roots:
        for relative in (os.path.join("Programs", "OpenAI", "Codex", "bin", "codex.exe"), os.path.join("OpenAI", "Codex", "bin", "codex.exe")):
            candidate = os.path.join(root, relative)
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def resolve_codex():
    """Resolve Codex from PATH, then from the fixed standard-install allowlist."""
    resolved = shutil.which("codex")
    if resolved:
        return resolved
    for candidate in _codex_candidates():
        if os.path.isfile(candidate):
            return candidate
    return None


def codex_environment(executable):
    environment = os.environ.copy()
    parent = os.path.dirname(executable)
    entries = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
    if parent and parent not in entries:
        environment["PATH"] = os.pathsep.join([parent] + entries)
    return environment

for directory in (
    QUEUE_DIR,
    WORKING_DIR,
    RESULT_DIR,
    COMPLETED_DIR,
    FAILED_DIR,
    LOG_DIR,
):
    os.makedirs(directory, exist_ok=True)


print("AI Bridge v0.1 시작")


file_sizes = {}


def write_log(message):
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_BYTES:
        archive_name = f"bridge-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        os.replace(LOG_FILE, get_available_path(LOG_DIR, archive_name))

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {message}\n")


def load_status():
    """Load the durable task history, tolerating a missing or damaged file."""
    default_status = {"updated_at": None, "tasks": {}}

    for path in (STATUS_FILE, STATUS_BACKUP_FILE):
        if not os.path.exists(path):
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                status = json.load(f)

            if not isinstance(status, dict) or not isinstance(status.get("tasks"), dict):
                raise ValueError("status.json 형식이 올바르지 않습니다.")

            status.setdefault("updated_at", None)
            if path == STATUS_BACKUP_FILE:
                write_log("status.json 백업에서 상태를 복구했습니다.")
            return status
        except Exception as e:
            write_log(f"{os.path.basename(path)} 읽기 실패: {e}")

    return default_status


def save_status():
    """Atomically replace status.json so an interrupted write does not corrupt it."""
    bridge_status["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary_file = f"{STATUS_FILE}.tmp"

    with open(temporary_file, "w", encoding="utf-8") as f:
        json.dump(bridge_status, f, ensure_ascii=False, indent=2)

    os.replace(temporary_file, STATUS_FILE)
    shutil.copy2(STATUS_FILE, STATUS_BACKUP_FILE)


def set_task_status(file, task_id, state, status_key=None, **details):
    status_key = status_key or task_id
    record = bridge_status["tasks"].get(status_key, {})
    now = datetime.now().isoformat(timespec="seconds")
    record.setdefault("attempt_count", 0)
    record.update({
        "task_id": task_id,
        "file": file,
        "status": state,
        "updated_at": now,
        **details
    })
    history = record.setdefault("history", [])
    history.append({"status": state, "at": now})
    del history[:-100]

    if state == "working":
        record["attempt_count"] += 1
        record["started_at"] = now
        record.pop("queued_at", None)
        record.pop("completed_at", None)
        record.pop("failed_at", None)
        record.pop("error", None)
    elif state == "queued":
        record["queued_at"] = now
        record.pop("completed_at", None)
        record.pop("failed_at", None)
        record.pop("error", None)
    elif state == "completed":
        record["completed_at"] = now
        record.pop("error", None)
    elif state == "failed":
        record["failed_at"] = now
        record["last_failed_at"] = now
        record["last_error"] = record.get("error")

    bridge_status["tasks"][status_key] = record
    save_status()


def get_task_id(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("task_id", fallback)
    except Exception:
        return fallback


def load_valid_task(path):
    with open(path, "r", encoding="utf-8") as f:
        task = json.load(f)

    if not isinstance(task, dict):
        raise ValueError("JSON 최상위 값은 객체여야 합니다.")

    task_id = task.get("task_id")
    instruction = task.get("instruction")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction은 비어 있지 않은 문자열이어야 합니다.")

    return task


def fail_input_file(source, file, task_id, error_message, status_key=None):
    failed_path = get_available_path(FAILED_DIR, file)
    shutil.move(source, failed_path)
    set_task_status(
        file,
        task_id,
        "failed",
        status_key=status_key,
        error=error_message,
        failed_file=os.path.basename(failed_path)
    )
    write_log(f"{file} 입력 실패 {error_message}")
    print(f"입력 실패: {file}")
    processed_files.add(file)


def get_available_path(directory, file):
    name, extension = os.path.splitext(file)
    candidate = os.path.join(directory, file)
    sequence = 1

    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{name}_{sequence}{extension}")
        sequence += 1

    return candidate


def import_archived_history():
    """Include tasks completed before status.json was introduced."""
    changed = False

    for directory, state in ((COMPLETED_DIR, "completed"), (FAILED_DIR, "failed")):
        for file in os.listdir(directory):
            if not file.endswith(".json"):
                continue

            path = os.path.join(directory, file)
            task_id = get_task_id(path, file)
            if task_id in bridge_status["tasks"]:
                continue

            timestamp = datetime.fromtimestamp(os.path.getmtime(path)).isoformat(
                timespec="seconds"
            )
            record = {
                "task_id": task_id,
                "file": file,
                "status": state,
                "updated_at": timestamp,
                "attempt_count": 1,
                "history": [{"status": state, "at": timestamp, "source": "archive"}],
            }
            record[f"{state}_at"] = timestamp
            bridge_status["tasks"][task_id] = record
            changed = True

    if changed:
        save_status()


def normalize_status_records():
    changed = False
    for record in bridge_status["tasks"].values():
        if "attempt_count" not in record:
            record["attempt_count"] = 1
            changed = True
        if "history" not in record:
            timestamp = record.get("updated_at") or datetime.now().isoformat(timespec="seconds")
            record["history"] = [{
                "status": record.get("status", "unknown"),
                "at": timestamp,
                "source": "migration"
            }]
            changed = True

    if changed:
        save_status()


def recover_working_files():
    """Return files left in working by an interrupted Bridge run to the queue."""
    for file in os.listdir(WORKING_DIR):
        if not file.endswith(".json"):
            continue

        working = os.path.join(WORKING_DIR, file)
        queued = os.path.join(QUEUE_DIR, file)
        task_id = get_task_id(working, file)

        if os.path.exists(queued):
            set_task_status(
                file,
                task_id,
                "interrupted",
                error="working과 queue에 같은 파일이 있어 자동 복구하지 않았습니다."
            )
            write_log(f"{file} 자동 복구 보류: queue에 같은 파일이 있습니다.")
            continue

        shutil.move(working, queued)
        set_task_status(file, task_id, "queued", recovered_at=datetime.now().isoformat(timespec="seconds"))
        write_log(f"{file} 작업 중단 후 queue로 복구")


def print_status():
    counts = {}
    for record in bridge_status["tasks"].values():
        state = record.get("status", "unknown")
        counts[state] = counts.get(state, 0) + 1

    print("\n[AI Bridge 상태]")
    print(f"마지막 갱신: {bridge_status.get('updated_at') or '-'}")
    summary = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
    print("작업 수: " + (summary or "작업 없음"))

    for task_id, record in sorted(bridge_status["tasks"].items()):
        timestamp = record.get("completed_at") or record.get("failed_at") or record.get("started_at") or record.get("queued_at") or "-"
        attempts = record.get("attempt_count", 0)
        print(f"- {task_id}: {record.get('status', 'unknown')} / 시도 {attempts}회 ({timestamp})")
        if record.get("error"):
            print(f"  오류: {record['error']}")


def print_status_json():
    print(json.dumps(bridge_status, ensure_ascii=False, indent=2))


def print_task_status(task_id):
    record = bridge_status["tasks"].get(task_id)
    if not record:
        raise ValueError(f"작업 ID를 찾을 수 없습니다: {task_id}")

    print(json.dumps(record, ensure_ascii=False, indent=2))


def print_task_history(task_id):
    record = bridge_status["tasks"].get(task_id)
    if not record:
        raise ValueError(f"작업 ID를 찾을 수 없습니다: {task_id}")

    for event in record.get("history", []):
        print(f"{event.get('at', '-')} | {event.get('status', 'unknown')}")


def print_task_result(task_id):
    record = bridge_status["tasks"].get(task_id)
    if not record:
        raise ValueError(f"작업 ID를 찾을 수 없습니다: {task_id}")
    if record.get("status") != "completed":
        raise ValueError(f"완료된 작업만 결과를 조회할 수 있습니다: {task_id}")

    result_file = record.get("result_file") or record["file"].replace(".json", "_result.json")
    result_path = os.path.join(RESULT_DIR, result_file)
    if not os.path.exists(result_path):
        raise FileNotFoundError(f"결과 파일을 찾을 수 없습니다: {result_path}")

    with open(result_path, "r", encoding="utf-8") as f:
        print(f.read())


def print_logs(line_count):
    if line_count < 1:
        raise ValueError("로그 줄 수는 1 이상이어야 합니다.")

    log_path = os.path.join(LOG_DIR, "bridge.log")
    if not os.path.exists(log_path):
        print("로그가 없습니다.")
        return

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines[-line_count:]:
        print(line, end="")


def print_queue():
    for label, directory in (("queue", QUEUE_DIR), ("working", WORKING_DIR), ("failed", FAILED_DIR)):
        files = sorted(file for file in os.listdir(directory) if file.endswith(".json"))
        print(f"{label}: {len(files)}")
        for file in files:
            print(f"- {file}")


def run_health_check():
    checks = [
        ("queue", QUEUE_DIR),
        ("working", WORKING_DIR),
        ("result", RESULT_DIR),
        ("completed", COMPLETED_DIR),
        ("failed", FAILED_DIR),
        ("logs", LOG_DIR),
        ("status.json", STATUS_FILE),
    ]
    healthy = True

    for label, path in checks:
        exists = os.path.exists(path)
        print(f"{'OK' if exists else 'FAIL'}  {label}")
        healthy = healthy and exists

    codex_available = resolve_codex() is not None
    print(f"{'OK' if codex_available else 'FAIL'}  codex")
    return healthy and codex_available


def run_self_test():
    with tempfile.TemporaryDirectory() as temporary_dir:
        valid_path = os.path.join(temporary_dir, "valid.json")
        invalid_path = os.path.join(temporary_dir, "invalid.json")

        with open(valid_path, "w", encoding="utf-8") as f:
            json.dump({"task_id": "SELF-TEST", "instruction": "test"}, f)
        load_valid_task(valid_path)

        with open(invalid_path, "w", encoding="utf-8") as f:
            json.dump({"task_id": "", "instruction": "test"}, f)
        try:
            load_valid_task(invalid_path)
        except ValueError:
            pass
        else:
            raise RuntimeError("잘못된 작업 입력을 감지하지 못했습니다.")

        collision_path = os.path.join(temporary_dir, "result.json")
        with open(collision_path, "w", encoding="utf-8") as f:
            f.write("test")
        if not get_available_path(temporary_dir, "result.json").endswith("result_1.json"):
            raise RuntimeError("파일명 충돌 처리가 올바르지 않습니다.")

    print("SELF-TEST PASSED")


def acquire_bridge_lock():
    handle = open(LOCK_FILE, "a+", encoding="utf-8")
    handle.seek(0)
    if not handle.read(1):
        handle.write("0")
        handle.flush()
    handle.seek(0)

    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise RuntimeError("이미 실행 중인 AI Bridge가 있습니다.")

    atexit.register(handle.close)
    return handle


def submit_task(task_id, instruction):
    if not task_id or not all(character.isalnum() or character in "-_" for character in task_id):
        raise ValueError("task_id에는 영문, 숫자, 하이픈, 밑줄만 사용할 수 있습니다.")
    if not instruction or not instruction.strip():
        raise ValueError("instruction은 비어 있을 수 없습니다.")
    if task_id in bridge_status["tasks"]:
        raise ValueError(f"이미 등록된 task_id입니다: {task_id}")

    file = f"{task_id.lower()}.json"
    destination = os.path.join(QUEUE_DIR, file)
    if os.path.exists(destination):
        raise FileExistsError(f"queue에 같은 파일이 이미 있습니다: {file}")

    temporary_path = f"{destination}.{os.getpid()}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(
            {"task_id": task_id, "instruction": instruction},
            f,
            ensure_ascii=False,
            indent=2
        )
    os.replace(temporary_path, destination)
    set_task_status(file, task_id, "queued")
    write_log(f"{task_id} 작업 등록")
    print(f"작업 등록: {task_id} ({file})")


def retry_task(task_id):
    record = bridge_status["tasks"].get(task_id)
    if not record:
        raise ValueError(f"작업 ID를 찾을 수 없습니다: {task_id}")

    if record.get("status") != "failed":
        raise ValueError(f"실패한 작업만 재시도할 수 있습니다: {task_id}")

    file = record["file"]
    source = os.path.join(FAILED_DIR, record.get("failed_file", file))
    destination = os.path.join(QUEUE_DIR, file)

    if not os.path.exists(source):
        raise FileNotFoundError(f"실패 파일을 찾을 수 없습니다: {source}")
    if os.path.exists(destination):
        raise FileExistsError(f"queue에 같은 파일이 이미 있습니다: {file}")

    try:
        with open(source, "r", encoding="utf-8") as f:
            failed_task = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"실패 파일을 수정한 뒤 재시도하세요: {e}")

    if failed_task.get("task_id") != task_id:
        raise ValueError("실패 파일의 task_id가 status.json 기록과 다릅니다.")

    shutil.move(source, destination)
    set_task_status(
        file,
        task_id,
        "queued",
        retried_at=datetime.now().isoformat(timespec="seconds"),
        retry_count=record.get("retry_count", 0) + 1
    )
    write_log(f"{task_id} 재시도 요청")
    print(f"재시도 대기열 등록: {task_id} ({file})")


def get_file_state(path):
    size = os.path.getsize(path)
    filename = os.path.basename(path)

    if filename not in file_sizes:
        file_sizes[filename] = size
        return "pending", None

    if file_sizes[filename] != size:
        file_sizes[filename] = size
        return "pending", None

    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return "ready", None
    except json.JSONDecodeError as e:
        return "invalid", f"JSON 형식 오류: {e}"
    except UnicodeDecodeError as e:
        return "invalid", f"UTF-8 인코딩 오류: {e}"
    except OSError as e:
        return "pending", str(e)


def run_codex(instruction, timeout_seconds=600):
    executable = resolve_codex()
    if not executable:
        raise Exception("필수 명령을 찾을 수 없습니다: codex (PATH 또는 표준 설치 경로 확인 필요)")
    result = subprocess.run(
        [
            executable,
            "exec",
            "--skip-git-repo-check",
            instruction
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=codex_environment(executable),
    )

    if result.returncode != 0:
        raise Exception(result.stderr)

    return result.stdout


bridge_status = load_status()
normalize_status_records()
import_archived_history()
save_status()
parser = argparse.ArgumentParser(description="AI Bridge 작업 처리기")
parser.add_argument("--status", action="store_true", help="저장된 작업 상태를 표시하고 종료합니다.")
parser.add_argument("--status-json", action="store_true", help="저장된 작업 상태를 JSON으로 출력하고 종료합니다.")
parser.add_argument("--task", metavar="TASK_ID", help="특정 작업의 상세 상태를 JSON으로 출력합니다.")
parser.add_argument("--history", metavar="TASK_ID", help="특정 작업의 상태 변경 이력을 출력합니다.")
parser.add_argument("--result", metavar="TASK_ID", help="완료된 작업의 결과를 출력합니다.")
parser.add_argument("--logs", type=int, nargs="?", const=50, help="최근 로그를 출력합니다. 기본값: 50줄")
parser.add_argument("--queue", action="store_true", help="대기·처리 중·실패 파일 목록을 출력합니다.")
parser.add_argument("--health", action="store_true", help="Bridge 실행 환경을 점검합니다.")
parser.add_argument("--self-test", action="store_true", help="핵심 입력·파일 처리 기능을 점검합니다.")
parser.add_argument("--submit", nargs=2, metavar=("TASK_ID", "INSTRUCTION"), help="새 작업을 queue에 등록합니다.")
parser.add_argument("--once", action="store_true", help="안정화 검사 후 대기열을 한 번 처리하고 종료합니다.")
parser.add_argument("--timeout", type=int, default=600, help="Codex 작업 제한 시간(초). 기본값: 600")
parser.add_argument("--retry", metavar="TASK_ID", help="실패한 작업을 queue로 되돌립니다.")
arguments = parser.parse_args()

if arguments.timeout < 1:
    print("실행 실패: timeout은 1 이상이어야 합니다.")
    raise SystemExit(1)

if arguments.status_json:
    print_status_json()
    raise SystemExit(0)

if arguments.status:
    print_status()
    raise SystemExit(0)

if arguments.task:
    try:
        print_task_status(arguments.task)
    except ValueError as e:
        print(f"조회 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

if arguments.history:
    try:
        print_task_history(arguments.history)
    except ValueError as e:
        print(f"이력 조회 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

if arguments.result:
    try:
        print_task_result(arguments.result)
    except (ValueError, FileNotFoundError) as e:
        print(f"결과 조회 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

if arguments.logs is not None:
    try:
        print_logs(arguments.logs)
    except ValueError as e:
        print(f"로그 조회 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

if arguments.queue:
    print_queue()
    raise SystemExit(0)

if arguments.health:
    raise SystemExit(0 if run_health_check() else 1)

if arguments.self_test:
    try:
        run_self_test()
    except Exception as e:
        print(f"SELF-TEST FAILED: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

if arguments.retry:
    try:
        retry_task(arguments.retry)
    except Exception as e:
        print(f"재시도 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

try:
    bridge_lock = acquire_bridge_lock()
except RuntimeError as e:
    print(f"실행 실패: {e}")
    raise SystemExit(1)

if arguments.submit:
    try:
        submit_task(*arguments.submit)
    except (ValueError, FileExistsError) as e:
        print(f"작업 등록 실패: {e}")
        raise SystemExit(1)
    raise SystemExit(0)

recover_working_files()
processed_files = {
    record.get("file") for record in bridge_status["tasks"].values()
    if record.get("status") in {"completed", "failed"}
}


scan_count = 0
completed_count = 0
failed_count = 0
while True:
    for file in os.listdir(QUEUE_DIR):
        if not file.endswith(".json"):
            continue

        if file in processed_files:
            continue

        source = os.path.join(QUEUE_DIR, file)

        file_state, file_error = get_file_state(source)

        if file_state == "pending":
            continue

        if file_state == "invalid":
            task_id = get_task_id(source, file)
            fail_input_file(source, file, task_id, file_error)
            failed_count += 1
            continue

        try:
            task = load_valid_task(source)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
            fail_input_file(source, file, get_task_id(source, file), str(e))
            failed_count += 1
            continue

        task_id = task["task_id"]
        existing = bridge_status["tasks"].get(task_id)
        if existing and existing.get("file") != file and existing.get("status") in {"completed", "failed"}:
            fail_input_file(
                source,
                file,
                task_id,
                "이미 완료 또는 실패 이력이 있는 task_id입니다.",
                status_key=f"{task_id} ({file})"
            )
            failed_count += 1
            continue

        working = os.path.join(WORKING_DIR, file)

        try:
            shutil.move(source, working)

            set_task_status(file, task_id, "working", timeout_seconds=arguments.timeout)
            print(f"\n작업 시작: {file}")

            output = run_codex(task["instruction"], arguments.timeout)

            result_file = get_available_path(
                RESULT_DIR,
                file.replace(".json", "_result.json")
            )

            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "task_id": task_id,
                        "status": "completed",
                        "result": output
                    },
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            completed_path = get_available_path(COMPLETED_DIR, file)
            shutil.move(working, completed_path)
            completed_count += 1
            set_task_status(
                file,
                task_id,
                "completed",
                result_file=os.path.basename(result_file),
                completed_file=os.path.basename(completed_path)
            )

            print(f"완료: {file}")
            processed_files.add(file)

        except Exception as e:
            task_id = get_task_id(working, file)
            error_message = str(e)
            failed_count += 1

            print(f"실패: {file}")
            write_log(f"{file} 실패 {error_message}")

            if os.path.exists(working):
                failed_path = get_available_path(FAILED_DIR, file)
                set_task_status(
                    file,
                    task_id,
                    "failed",
                    error=error_message,
                    failed_file=os.path.basename(failed_path)
                )
                shutil.move(working, failed_path)
            else:
                set_task_status(file, task_id, "failed", error=error_message)

            processed_files.add(file)

    if arguments.once:
        scan_count += 1
        if scan_count >= 2:
            print(f"단일 실행 완료: completed {completed_count}, failed {failed_count}")
            break

    time.sleep(1)
