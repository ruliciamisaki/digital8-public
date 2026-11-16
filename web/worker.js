"use strict";

const runtimeUrl = "https://cdn.jsdelivr.net/pyodide/v0.29.4/full/";
const incoming = "/tmp/digital8-input";
const output = "/tmp/digital8-output";
let runtime;

async function initialize() {
  importScripts(runtimeUrl + "pyodide.js");
  const python = await loadPyodide({ indexURL: runtimeUrl, packages: ["numpy", "Pillow"] });
  const response = await fetch("./engine.zip");
  if (!response.ok) throw new Error("V4の変換コードを読み込めません。");
  python.unpackArchive(await response.arrayBuffer(), "zip", { extractDir: "/digital8" });
  python.runPython(`
import sys
sys.path.insert(0, "/digital8")
from engine import Job, dispatch
digital8_job = Job()
def digital8_request(request):
    return dispatch(request, digital8_job)
`);
  return { python, request: python.globals.get("digital8_request") };
}

self.onmessage = async ({ data: { id, bytes, ...message } }) => {
  try {
    runtime ??= initialize().catch(error => {
      runtime = undefined;
      throw error;
    });
    const { python, request } = await runtime;
    if (message.action === "open") {
      python.FS.writeFile(incoming, new Uint8Array(bytes));
      try {
        const result = JSON.parse(request(JSON.stringify({ ...message, incoming })));
        self.postMessage({ id, result });
      } finally {
        if (python.FS.analyzePath(incoming).exists) python.FS.unlink(incoming);
      }
      return;
    }
    try {
      const result = JSON.parse(request(JSON.stringify({ ...message, output })));
      const data = python.FS.readFile(output);
      self.postMessage({ id, result, data }, [data.buffer]);
    } finally {
      if (python.FS.analyzePath(output).exists) python.FS.unlink(output);
    }
  } catch (error) {
    const lines = String(error.message || error).trim().split("\n");
    const detail = lines.at(-1).replace(/^(?:ValueError|OSError|UnidentifiedImageError):\s*/, "");
    self.postMessage({ id, error: detail });
  }
};
