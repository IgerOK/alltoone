// Drives the real context-budget plugin source with a stub plugin runtime.
//
// The plugin imports `@opencode/plugin`, which only exists inside OpenCode, so
// the source is copied with that single import replaced by a stub. Everything
// else — thresholds, the usage calculation, the compaction guard — is the code
// that actually runs in a session.
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
function makeContext(autoCompact, usedTokens) {
  const state = { hook: null, compactions: [], systems: [] }
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
        data: [{ type: "assistant", tokens: { input: usedTokens, output: 100 } }],
      }),
      compact: async ({ sessionID }) => {
        state.compactions.push(sessionID)
        return {}
      },
    },
  }
  return { ctx, state }
}

async function run(autoCompact, usedTokens, sessionID, calls) {
  const { ctx, state } = makeContext(autoCompact, usedTokens)
  await plugin.setup(ctx)
  if (!state.hook) throw new Error("the context hook was not registered")
  for (let index = 0; index < calls; index++) {
    const event = {
      sessionID,
      model: { providerID: "test", id: "model" },
      messages: [],
      system: [],
    }
    await state.hook(event)
    state.systems.push(event.system.map((part) => part.text).join("\n"))
  }
  return state
}

// 95% of the window: critical, whatever the exact reservation is.
const CRITICAL_USED = 190_000
const NORMAL_USED = 10_000

const criticalOn = await run(true, CRITICAL_USED, "ses_critical_on", 3)
check("autoCompact=true requests compaction", criticalOn.compactions.length, 1)
check("autoCompact=true keeps one request per session", criticalOn.compactions[0], "ses_critical_on")
check(
  "directive warns that compaction is lossy",
  criticalOn.systems.some((text) => text.includes("compaction was requested, but it loses detail")),
  true,
)
check(
  "meter reports critical",
  criticalOn.systems[0].includes("mode=critical"),
  true,
)

const criticalOff = await run(false, CRITICAL_USED, "ses_critical_off", 2)
check("autoCompact=false never compacts", criticalOff.compactions.length, 0)
check(
  "directive stays plain when off",
  criticalOff.systems.some((text) => text.includes("compaction was requested")),
  false,
)

const belowThreshold = await run(true, NORMAL_USED, "ses_normal", 2)
check("no compaction below the threshold", belowThreshold.compactions.length, 0)
check("meter reports normal", belowThreshold.systems[0].includes("mode=normal"), true)

const log = readFileSync(join(work, ".opencode", "handoff", "budget.log"), "utf8")
check("trace records the request", log.includes("compact requested ses_critical_on"), true)
check("trace records autoCompact=false", log.includes("setup autoCompact=false"), true)

if (failures.length > 0) {
  console.error(`\n${failures.length} failing check(s)`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}
console.log("\nall checks passed")
