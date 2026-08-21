import json
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_FILE = os.path.join(BASE_DIR, "bridge.py")
STATUS_FILE = os.path.join(BASE_DIR, "status.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "bridge.log")
RESULT_DIR = os.path.join(BASE_DIR, "result")


class BridgeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Bridge")
        self.geometry("1050x720")
        self.minsize(850, 600)
        self.bridge_process = None

        self._build_ui()
        self.refresh()
        self.set_next_task_id()
        self.after(3000, self.auto_refresh)

    def _build_ui(self):
        header = ttk.Frame(self, padding=16)
        header.pack(fill="x")

        ttk.Label(header, text="AI Bridge", font=("Segoe UI", 20, "bold")).pack(side="left")
        self.bridge_state = ttk.Label(header, text="상태 확인 중")
        self.bridge_state.pack(side="left", padx=16)

        self.start_button = ttk.Button(header, text="Bridge 시작", command=self.start_bridge)
        self.start_button.pack(side="right")
        ttk.Button(header, text="새로고침", command=self.refresh).pack(side="right", padx=8)

        task_box = ttk.LabelFrame(self, text="새 작업", padding=12)
        task_box.pack(fill="x", padx=16, pady=(0, 8))

        ttk.Label(task_box, text="작업 ID").grid(row=0, column=0, sticky="w")
        self.task_id = ttk.Entry(task_box, width=22, state="readonly")
        self.task_id.grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(task_box, text="지시 내용").grid(row=0, column=2, sticky="nw")
        self.instruction = tk.Text(task_box, height=3, wrap="word")
        self.instruction.grid(row=0, column=3, sticky="ew", padx=8)
        ttk.Button(task_box, text="작업 등록", command=self.submit_task).grid(row=0, column=4, padx=(8, 0))
        task_box.columnconfigure(3, weight=1)

        center = ttk.PanedWindow(self, orient="horizontal")
        center.pack(fill="both", expand=True, padx=16, pady=8)

        task_frame = ttk.LabelFrame(center, text="작업 상태", padding=8)
        center.add(task_frame, weight=3)
        columns = ("task_id", "status", "attempts", "updated")
        self.task_table = ttk.Treeview(task_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"task_id": "작업 ID", "status": "상태", "attempts": "시도", "updated": "최근 변경"}
        widths = {"task_id": 180, "status": 100, "attempts": 60, "updated": 170}
        for column in columns:
            self.task_table.heading(column, text=headings[column])
            self.task_table.column(column, width=widths[column], anchor="center")
        self.task_table.pack(fill="both", expand=True)
        self.task_table.bind("<Double-1>", lambda event: self.show_result())

        action_bar = ttk.Frame(task_frame)
        action_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(action_bar, text="결과 보기", command=self.show_result).pack(side="left")
        ttk.Button(action_bar, text="실패 작업 재시도", command=self.retry_task).pack(side="left", padx=8)
        ttk.Button(action_bar, text="상세 상태", command=self.show_details).pack(side="left")

        log_frame = ttk.LabelFrame(center, text="최근 기록", padding=8)
        center.add(log_frame, weight=2)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def run_bridge_command(self, *arguments):
        return subprocess.run(
            [sys.executable, BRIDGE_FILE, *arguments],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def start_bridge(self):
        if self.bridge_process and self.bridge_process.poll() is None:
            messagebox.showinfo("AI Bridge", "Bridge가 이미 실행 중입니다.")
            return

        self.bridge_process = subprocess.Popen(
            [sys.executable, BRIDGE_FILE],
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.bridge_state.config(text="Bridge 실행 중")
        self.start_button.config(text="Bridge 중지", command=self.stop_bridge)

    def stop_bridge(self):
        if self.bridge_process and self.bridge_process.poll() is None:
            self.bridge_process.terminate()
            self.bridge_process.wait(timeout=5)
        self.bridge_process = None
        self.bridge_state.config(text="Bridge 중지됨")
        self.start_button.config(text="Bridge 시작", command=self.start_bridge)

    def submit_task(self):
        task_id = self.task_id.get().strip()
        instruction = self.instruction.get("1.0", "end").strip()
        if not task_id or not instruction:
            messagebox.showwarning("입력 확인", "작업 ID와 지시 내용을 입력하세요.")
            return

        try:
            result = self.run_bridge_command("--submit", task_id, instruction)
        except subprocess.TimeoutExpired:
            messagebox.showerror("작업 등록 실패", "시간이 초과되었습니다.")
            return

        if result.returncode != 0:
            messagebox.showerror("작업 등록 실패", result.stdout or result.stderr)
            return

        self.instruction.delete("1.0", "end")
        self.refresh()
        self.set_next_task_id()

    def retry_task(self):
        task_id = self.selected_task_id()
        if not task_id:
            return

        result = self.run_bridge_command("--retry", task_id)
        if result.returncode != 0:
            messagebox.showerror("재시도 실패", result.stdout or result.stderr)
            return
        self.refresh()

    def show_result(self):
        task_id = self.selected_task_id()
        if not task_id:
            return

        record = self.read_status().get("tasks", {}).get(task_id)
        if not record or record.get("status") != "completed":
            messagebox.showinfo("결과", "완료된 작업만 결과를 볼 수 있습니다.")
            return

        result_file = record.get("result_file") or record["file"].replace(".json", "_result.json")
        result_path = os.path.join(RESULT_DIR, result_file)
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            content = "결과 파일을 찾을 수 없습니다."
        self.show_text_window(f"결과: {task_id}", content)

    def show_details(self):
        task_id = self.selected_task_id()
        if not task_id:
            return

        record = self.read_status().get("tasks", {}).get(task_id)
        if not record:
            messagebox.showinfo("상세 상태", "작업 정보를 찾을 수 없습니다.")
            return
        self.show_text_window(
            f"상세 상태: {task_id}",
            json.dumps(record, ensure_ascii=False, indent=2)
        )

    def selected_task_id(self):
        selection = self.task_table.selection()
        if not selection:
            messagebox.showinfo("AI Bridge", "작업을 먼저 선택하세요.")
            return None
        return self.task_table.item(selection[0], "values")[0]

    def show_text_window(self, title, content):
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("760x520")
        text = scrolledtext.ScrolledText(window, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", content)
        text.config(state="disabled")

    def read_status(self):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"tasks": {}}

    def set_next_task_id(self):
        highest_number = 0
        for task_id in self.read_status().get("tasks", {}):
            match = re.fullmatch(r"TASK-(\d+)", task_id)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        self.task_id.config(state="normal")
        self.task_id.delete(0, "end")
        self.task_id.insert(0, f"TASK-{highest_number + 1:04d}")
        self.task_id.config(state="readonly")

    def refresh(self):
        status = self.read_status()
        tasks = status.get("tasks", {})
        for item in self.task_table.get_children():
            self.task_table.delete(item)
        for task_id, record in sorted(tasks.items()):
            self.task_table.insert(
                "", "end", values=(
                    task_id,
                    record.get("status", "unknown"),
                    record.get("attempt_count", 0),
                    record.get("updated_at", "-"),
                )
            )

        is_running = self.bridge_process and self.bridge_process.poll() is None
        self.bridge_state.config(text="Bridge 실행 중" if is_running else "Bridge 중지됨")
        self.start_button.config(
            text="Bridge 중지" if is_running else "Bridge 시작",
            command=self.stop_bridge if is_running else self.start_bridge,
        )
        self.refresh_logs()

    def refresh_logs(self):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                content = "".join(f.readlines()[-80:])
        except FileNotFoundError:
            content = "기록이 없습니다."

        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", content)
        self.log_text.config(state="disabled")

    def auto_refresh(self):
        self.refresh()
        self.after(3000, self.auto_refresh)

    def on_close(self):
        if self.bridge_process and self.bridge_process.poll() is None:
            if messagebox.askyesno("AI Bridge", "Bridge도 함께 중지할까요?"):
                self.stop_bridge()
        self.destroy()


if __name__ == "__main__":
    BridgeApp().mainloop()
