import readline from "node:readline";
import { spawn } from "node:child_process";

const agy = process.env.AGY_PATH || "C:\\Users\\movie\\AppData\\Local\\agy\\bin\\agy.exe";

function reply(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function error(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

function askAntigravity(prompt) {
  return new Promise((resolve) => {
    const child = spawn(agy, [
      "--print", prompt, "--output-format", "text", "--print-timeout", "120s",
    ], { shell: false, windowsHide: true });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      resolve("ERROR: Antigravity request timed out after 135 seconds.");
    }, 135000);
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve(`ERROR: Could not start Antigravity: ${err.message}`);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      const output = (stdout || stderr).trim();
      resolve(output || `ERROR: Antigravity exited with code ${code}.`);
    });
  });
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (msg.method === "notifications/initialized") return;
  if (msg.method === "initialize") {
    return reply(msg.id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "antigravity", version: "1.0.0" },
    });
  }
  if (msg.method === "tools/list") {
    return reply(msg.id, { tools: [{
      name: "ask_antigravity",
      description: "Send a prompt to Google Antigravity CLI and return its response.",
      inputSchema: {
        type: "object", properties: { prompt: { type: "string" } }, required: ["prompt"],
      },
    }] });
  }
  if (msg.method === "tools/call") {
    if (msg.params?.name !== "ask_antigravity") return error(msg.id, -32602, "Unknown tool");
    const prompt = msg.params?.arguments?.prompt;
    if (typeof prompt !== "string" || !prompt.trim()) return error(msg.id, -32602, "prompt must be a non-empty string");
    const text = await askAntigravity(prompt);
    return reply(msg.id, { content: [{ type: "text", text }] });
  }
  if (msg.id !== undefined) return error(msg.id, -32601, `Method not found: ${msg.method}`);
});
