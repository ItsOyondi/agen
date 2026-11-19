import { useRef, useState } from "react";

const STEPS = [
  {
    id: "fetch",
    title: "fetch",
    detail: "Read today's prices. A timeout here is a transient failure.",
  },
  {
    id: "analyze",
    title: "analyze",
    detail: "Ask the model for a short summary.",
  },
  {
    id: "publish",
    title: "publish",
    detail: "Write the report. This step has not run yet if the process dies.",
  },
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default function WorkflowDemo() {
  const [finished, setFinished] = useState([]);
  const [replayed, setReplayed] = useState([]);
  const [active, setActive] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [journal, setJournal] = useState("No run yet. The journal is empty.");
  const runId = useRef(0);

  function reset() {
    runId.current += 1;
    setFinished([]);
    setReplayed([]);
    setActive(null);
    setPhase("idle");
    setJournal("No run yet. The journal is empty.");
  }

  async function play({ crashAt }) {
    const id = ++runId.current;
    const alive = () => runId.current === id;
    setFinished([]);
    setReplayed([]);
    setPhase("running");
    setJournal("Run started. Each finished step is written to the journal.");
    const done = [];
    for (const step of STEPS) {
      if (!alive()) return;
      setActive(step.id);
      await wait(650);
      if (!alive()) return;
      done.push(step.id);
      setFinished([...done]);
      setActive(null);
      setJournal(`Saved "${step.title}". A crash after this point can resume here.`);
      if (crashAt === step.id) {
        setPhase("crashed");
        setJournal(
          `The process stopped after "${step.title}". Finished steps stay in the journal. "publish" was never started.`
        );
        return;
      }
    }
    if (!alive()) return;
    setPhase("done");
    setJournal("The workflow finished. Every step is in the journal.");
  }

  async function resume() {
    const id = ++runId.current;
    const alive = () => runId.current === id;
    const already = [...finished];
    setReplayed(already);
    setPhase("resuming");
    setJournal("Resume loaded the journal. Finished steps are replayed from memory, not run again.");
    const remaining = STEPS.filter((step) => !already.includes(step.id));
    const done = [...already];
    for (const step of remaining) {
      if (!alive()) return;
      setActive(step.id);
      await wait(650);
      if (!alive()) return;
      done.push(step.id);
      setFinished([...done]);
      setActive(null);
    }
    if (!alive()) return;
    setPhase("done");
    setJournal("Resumed run finished. Only the unfinished step did new work.");
  }

  function statusFor(stepId) {
    if (replayed.includes(stepId)) return "replayed";
    if (finished.includes(stepId)) return "saved";
    if (active === stepId) return "running";
    return "waiting";
  }

  const busy = phase === "running" || phase === "resuming";

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <div className="rounded-3xl border border-white/10 bg-navy/80 p-5 shadow-[0_20px_80px_rgba(0,0,0,0.25)]">
        <ol className="space-y-3">
          {STEPS.map((step, index) => {
            const status = statusFor(step.id);
            return (
              <li
                key={step.id}
                className="flex items-start gap-4 rounded-2xl border border-white/10 bg-ink/50 px-4 py-4"
              >
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/5 text-sm text-lilac">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-medium">{step.title}</p>
                    <StatusBadge status={status} />
                  </div>
                  <p className="mt-1 text-sm text-mist">{step.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => play({ crashAt: null })}
            className="rounded-full bg-violet px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Run to completion
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => play({ crashAt: "analyze" })}
            className="rounded-full border border-white/15 px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            Crash after analyze
          </button>
          <button
            type="button"
            disabled={phase !== "crashed"}
            onClick={resume}
            className="rounded-full border border-lilac/50 px-4 py-2 text-sm font-medium text-lilac disabled:opacity-40"
          >
            Resume from journal
          </button>
          <button
            type="button"
            onClick={reset}
            className="rounded-full px-4 py-2 text-sm text-mist hover:text-white"
          >
            Reset
          </button>
        </div>
      </div>
      <aside className="flex flex-col justify-between rounded-3xl border border-white/10 bg-panel/70 p-6">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-lilac">Journal</p>
          <p className="mt-3 text-lg leading-snug">{journal}</p>
        </div>
        <p className="mt-8 text-sm text-mist">
          A real Agen workflow writes this record beside the program. Restart the same
          command and finished steps are not repeated.
        </p>
      </aside>
    </div>
  );
}

function StatusBadge({ status }) {
  const styles = {
    waiting: "bg-white/5 text-mist",
    running: "bg-violet/20 text-lilac",
    saved: "bg-emerald-400/15 text-emerald-300",
    replayed: "bg-sky-400/15 text-sky-200",
  };
  const labels = {
    waiting: "waiting",
    running: "running",
    saved: "saved",
    replayed: "replayed",
  };
  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}
