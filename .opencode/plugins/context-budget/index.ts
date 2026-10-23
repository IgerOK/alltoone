import { appendFileSync, existsSync, mkdirSync } from "node:fs"
import { join } from "node:path"

import { Plugin } from "@opencode/plugin"

/**
 * Injects a one-line context budget into every model request and turns it into
 * an action when the window fills up.
 *
 * The meter is deliberately outside the model: the model never counts tokens,
 * it only reads the number and follows the policy from AGENTS.md.
 */

const CHECKPOINT = 0.7
const HANDOFF = 0.85
const CRITICAL = 0.92

type Mode = "normal" | "checkpoint" | "handoff" | "critical"

function fmt(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 10_000) return `${Math.round(value / 1000)}k`
  if (value >= 1_000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

/** Rough size of a message, used only for content that has no provider usage. */
function approx(text: string): number {
  let ascii = 0
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) < 128) ascii++
  return Math.ceil(ascii / 4 + (text.length - ascii) / 1.8)
}

/**
 * Tokens a finished request occupied in the window.
 *
 * Providers disagree on whether `input` already contains the cached part, so
 * the larger of the two interpretations is used: overestimating only makes the
 * warning appear earlier.
 */
function usageTotal(tokens: any): number {
  if (!tokens) return 0
  const read = tokens.cache?.read ?? 0
  const write = tokens.cache?.write ?? 0
  const input = tokens.input ?? 0
  const prompt = input >= read ? input : input + read + write
  return prompt + (tokens.output ?? 0) + (tokens.reasoning ?? 0)
}

function directive(mode: Mode, autoCompact: boolean): string {
  if (mode === "checkpoint") {
    return (
      "\nContext crossed 70%. Before starting anything new, update `.opencode/handoff/STATE.md` " +
      "(goal, done, next, decisions, files). Keep it under 60 lines."
    )
  }
  if (mode === "handoff") {
    return (
      "\nContext crossed 85%. Do not start new work. Finish `.opencode/handoff/STATE.md`, " +
      "refresh `.opencode/handoff/files.txt`, run `python -m alltoone handoff`, " +
      "then tell the user to open a new chat and run `/resume`."
    )
  }
  if (mode === "critical") {
    return (
      "\nContext crossed 92%. Stop calling tools. Write `.opencode/handoff/STATE.md` now " +
      "and tell the user to start a new chat with `/resume`." +
      (autoCompact ? " A compaction was requested, but it loses detail: the handoff is still the safe exit." : "")
    )
  }
  return ""
}

function trace(directory: string, text: string): void {
  try {
    const dir = join(directory, ".opencode", "handoff")
    mkdirSync(dir, { recursive: true })
    appendFileSync(join(dir, "budget.log"), `${new Date().toISOString()} ${text}\n`)
  } catch {
    // Tracing is best-effort.
  }
}

export default Plugin.define({
  id: "alltoone.context-budget",
  async setup(ctx) {
    const lastMode = new Map<string, Mode>()
    const reported = new Set<string>()
    const compactionRequested = new Set<string>()
    // Compaction rewrites history and loses detail, so it is an explicit
    // per-project opt-in: the default exit is a new chat with `/resume`.
    const autoCompact = ctx.options.autoCompact === true
    // Auto-resume only points at the pack; it never reads it. Off by default:
    // the pack costs ~30k tokens and only a continuing session should pay.
    const autoResume = ctx.options.autoResume === true
    const pack = join(ctx.location.directory, ".opencode", "handoff", "pack.md")
    const resumed = new Set<string>()
    trace(ctx.location.directory, `setup autoCompact=${autoCompact} autoResume=${autoResume}`)

    await ctx.session.hook("context", async (event) => {
      try {
        const context: any = await ctx.session.context({ sessionID: event.sessionID })
        const messages: any[] = context?.data ?? []

        let used = 0
        let lastAssistant = -1
        for (let i = 0; i < messages.length; i++) {
          const message = messages[i]
          if (message.type === "assistant" && message.tokens) {
            used = usageTotal(message.tokens)
            lastAssistant = i
          }
        }

        if (lastAssistant >= 0) {
          for (let i = lastAssistant + 1; i < messages.length; i++) {
            used += approx(JSON.stringify(messages[i]))
          }
        } else {
          used = approx(JSON.stringify(event.messages))
        }

        const catalog: any = await ctx.model.list()
        const models: any[] = catalog?.data ?? []
        const info = models.find(
          (model) =>
            model.providerID === event.model.providerID &&
            (model.id === event.model.id || model.modelID === event.model.id),
        )
        const window: number = info?.limit?.context ?? 200_000
        const maxOutput: number = info?.limit?.output ?? 8_000
        // The response must fit too, plus a small margin for the estimate of
        // messages that arrived after the last provider usage report.
        const reserve = maxOutput + Math.round(window * 0.02)

        const ratio = used / window
        const left = Math.max(0, window - used)
        const mode: Mode =
          ratio >= CRITICAL ? "critical" : ratio >= HANDOFF ? "handoff" : ratio >= CHECKPOINT ? "checkpoint" : "normal"

        const line =
          `[budget] ${fmt(used)}/${fmt(window)} used (${Math.round(ratio * 100)}%)` +
          ` · left ${fmt(left)} · reserve ${fmt(reserve)} · mode=${mode}`

        // Critical is the last moment a compaction can still be admitted, and
        // the request only runs at the next step boundary — not inside this
        // call. It is sent once per session so a failed attempt cannot loop.
        if (mode === "critical" && autoCompact && !compactionRequested.has(event.sessionID)) {
          compactionRequested.add(event.sessionID)
          try {
            await ctx.session.compact({ sessionID: event.sessionID })
            trace(ctx.location.directory, `compact requested ${event.sessionID}`)
          } catch (error) {
            trace(ctx.location.directory, `compact failed: ${String(error)}`)
          }
        }

        const previous = lastMode.get(event.sessionID)
        const changed = previous !== mode
        lastMode.set(event.sessionID, mode)
        if (!reported.has(event.sessionID)) {
          reported.add(event.sessionID)
          trace(ctx.location.directory, line)
        } else if (changed) {
          // Transitions are the interesting part of the log: they show when a
          // directive was handed to the model and how fast the window filled.
          trace(ctx.location.directory, `mode ${previous} -> ${mode} (${Math.round(ratio * 100)}%)`)
        }
        // A fresh chat starts blind. One pointer on the first request is
        // cheaper than making the user retype `/resume`, and it repeats only
        // once per session so the pack never displaces real context.
        let resume = ""
        if (autoResume && !resumed.has(event.sessionID) && existsSync(pack)) {
          resumed.add(event.sessionID)
          resume =
            "\nA handoff pack is ready at `.opencode/handoff/pack.md`: read it first " +
            "when work continues from a previous session."
          trace(ctx.location.directory, `resume pointer ${event.sessionID}`)
        }
        const text = line + (changed ? directive(mode, autoCompact) : "") + resume
        // A trailing message leaves the system prompt byte-identical between
        // requests, which is what prompt caching keys on: injecting into
        // `event.system` dropped the cache hit rate from ~97% to ~2%
        // (measured 2026-10-23 in a live session).
        const outgoing: any[] = (event as any).messages
        if (Array.isArray(outgoing)) {
          outgoing.push({ role: "system", content: [{ type: "text", text }] })
        } else {
          event.system.push({ type: "text", text })
        }
      } catch (error) {
        // A broken meter must never break the request.
        trace(ctx.location.directory, `error: ${String(error)}`)
      }
    })
  },
})
