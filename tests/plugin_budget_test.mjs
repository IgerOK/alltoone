// Drives the real context-budget plugin source with a stub plugin runtime.
//
// The plugin imports `@opencode/plugin`, which only exists inside OpenCode, so
// the source is copied with that single import replaced by a stub. Everything
// else — thresholds, the usage calculation, the compaction guard, the place the
// line is injected — is the code that actually runs in a session.
//
// Usage: node tests/plugin_budget_test.mjs [path/to/index.ts]

import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const here = fileURLToPath(new URL(".", import.meta.url))
const source = process.argv[2] ?? join(here, "..", ".opencode", "plugins", "context-budget", "index.ts")

const IMPORT = 'import { Plugin } from "@opencode/plugin"'
const STUB = "const Plugin = { define: (definition) => definition }"

const text = readFileSync(source, "utf8")
if (!text.includes(IMPORT)) throw new Error(`import line not found in ${source}`)
const work = mkdtempSync(join(tmpdir(), "budget-test-"))
const modulePath = join(work, "plugin.mts")
writeFileSync(modulePath, text.replace(IMPORT, STUB))

const plugin = (await import(pathToFileURL(modulePath).href)).default

const WINDOW = 200_000
const failures = []

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${JSON.stringify(actual)}`)
  if (!ok) failures.push(`${name}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
}

/** A plugin context that reports fixed provider usage and records compaction. */
function makeContext(autoCompact) {
  const state = { hook: null, compactions: [], used: 0 }
  const ctx = {
    options: { autoCompact },
    location: { directory: work },
    model: {
      list: async () => ({
        data: [{ providerID: "test", id: "model", limit: { context: WINDOW, output: 8_000 } }],
      }),
    },
    session: {
      hook: async (name, callback) => {
        if (name === "context") state.hook = callback
      },
      context: async () => ({
        data: [{ type: "assistant", tokens: { input: state.used, output: 100 } }],
      }),
      compact: async ({ sessionID }) => {
        state.compactions.push(sessionID)
        return {}
      },
    },
  }
  return { ctx, state }
}

/** Run the hook once per usage value, as if the window grew between requests. */
async function run(autoCompact, usage, sessionID) {
  const { ctx, state } = makeContext(autoCompact)
  state.tails = []
  state.systemSizes = []
  await plugin.setup(ctx)
  if (!state.hook) throw new Error("the context hook was not registered")
  for (const tokens of usage) {
    state.used = tokens
    const event = {
      sessionID,
      model: { providerID: "test", id: "model" },
      messages: [],
      system: [],
    }
    await state.hook(event)
    const last = event.messages[event.messages.length - 1]
    state.tails.push(last?.content?.[0]?.text ?? "")
    state.systemSizes.push(event.system.length)
  }
  return state
}

// 95% of the window: critical, whatever the exact reservation is.
const CRITICAL_USED = 190_000
const NORMAL_USED = 10_000

const criticalOn = await run(true, [CRITICAL_USED, CRITICAL_USED, CRITICAL_USED], "ses_critical_on")
check("autoCompact=true requests compaction", criticalOn.compactions.length, 1)
check("autoCompact=true keeps one request per session", criticalOn.compactions[0], "ses_critical_on")
check(
  "directive warns that compaction is lossy",
  criticalOn.tails.some((text) => text.includes("compaction was requested, but it loses detail")),
  true,
)
check("meter travels in a trailing message", criticalOn.tails[0].includes("mode=critical"), true)
check("system prompt stays untouched", criticalOn.systemSizes, [0, 0, 0])

const criticalOff = await run(false, [CRITICAL_USED, CRITICAL_USED], "ses_critical_off")
check("autoCompact=false never compacts", criticalOff.compactions.length, 0)
check(
  "directive stays plain when off",
  criticalOff.tails.some((text) => text.includes("compaction was requested")),
  false,
)

const belowThreshold = await run(true, [NORMAL_USED, NORMAL_USED], "ses_normal")
check("no compaction below the threshold", belowThreshold.compactions.length, 0)
check("meter reports normal", belowThreshold.tails[0].includes("mode=normal"), true)
check("system prompt stays untouched below the threshold", belowThreshold.systemSizes, [0, 0])

const transition = await run(
  true,
  [NORMAL_USED, CRITICAL_USED, CRITICAL_USED],
  "ses_transition",
)
check("compaction waits for the threshold", transition.compactions.length, 1)
check(
  "mode change reaches the model",
  [transition.tails[0].includes("mode=normal"), transition.tails[1].includes("mode=critical")],
  [true, true],
)

const log = readFileSync(join(work, ".opencode", "handoff", "budget.log"), "utf8")
check("trace records the request", log.includes("compact requested ses_critical_on"), true)
check("trace records autoCompact=false", log.includes("setup autoCompact=false"), true)
check("trace records the mode transition", log.includes("mode normal -> critical"), true)

if (failures.length > 0) {
  console.error(`\n${failures.length} failing check(s)`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}
console.log("\nall checks passed")
