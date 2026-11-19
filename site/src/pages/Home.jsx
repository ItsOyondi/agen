import { Link } from "react-router-dom";
import WorkflowDemo from "../components/WorkflowDemo.jsx";

const points = [
  {
    title: "The problem",
    body: "A long agent or automation dies on a timeout, a rate limit, or a deploy, and someone has to notice, restart it, and hope it does not repeat work it already finished.",
  },
  {
    title: "The objective",
    body: "Write the workflow once. Agen retries transient failures, checkpoints each step, restarts a service, and keeps infrastructure in the same file as the program.",
  },
  {
    title: "The long-term goal",
    body: "Be the language people choose when an agent or automation must keep running — with parallel I/O and a fast path for pure functions, so reliability is not slow.",
  },
];

export default function Home() {
  return (
    <>
      <section className="mx-auto grid max-w-6xl items-center gap-10 px-5 pb-16 pt-16 lg:grid-cols-[1.15fr_0.85fr] lg:pt-24">
        <div>
          <p className="text-sm font-medium uppercase tracking-[0.2em] text-lilac">
            Agent language
          </p>
          <h1 className="mt-4 max-w-xl text-4xl font-semibold leading-tight tracking-tight sm:text-6xl">
            You write the workflow. Agen keeps it running.
          </h1>
          <p className="mt-5 max-w-xl text-lg text-mist">
            Retries, checkpoints, and resume belong in the language. You do not
            hand-write backoff loops, a supervisor, and a separate deploy project
            for every agent.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              to="/download"
              className="rounded-full bg-violet px-5 py-3 text-sm font-semibold text-white"
            >
              Download the zip
            </Link>
            <a
              href="#try-it"
              className="rounded-full border border-white/15 px-5 py-3 text-sm font-semibold"
            >
              Try a crash and resume
            </a>
          </div>
        </div>
        <div className="rounded-3xl border border-white/10 bg-navy/70 p-6">
          <img src="/agen-logo.png" alt="Agen" className="w-full max-w-md" />
          <pre className="mt-6 overflow-x-auto rounded-2xl bg-ink p-4 text-sm leading-7 text-[#d6deea]">
            <code>
              <span className="text-[#C586C0]">workflow</span> daily_report(day) {"{"}
              {"\n"}
              {"    "}
              <span className="text-[#C586C0]">step</span> fetch{" "}
              <span className="text-[#C586C0]">with</span> {"{"}retries: 4{"}"} {"{"}
              {"\n"}
              {"        "}prices = http_get(day)
              {"\n"}
              {"    "}
              {"}"}
              {"\n"}
              {"    "}
              <span className="text-[#C586C0]">step</span> analyze{" "}
              <span className="text-[#C586C0]">with</span> {"{"}timeout: 30{"}"} {"{"}
              {"\n"}
              {"        "}summary = ask(analyst, prices)
              {"\n"}
              {"    "}
              {"}"} <span className="text-[#C586C0]">fallback</span> {"{"}
              {"\n"}
              {"        "}summary = <span className="text-[#CE9178]">"analysis unavailable"</span>
              {"\n"}
              {"    "}
              {"}"}
              {"\n"}
              {"    "}
              <span className="text-[#C586C0]">return</span> summary
              {"\n"}
              {"}"}
            </code>
          </pre>
        </div>
      </section>

      <section id="try-it" className="mx-auto max-w-6xl px-5 py-8">
        <div className="mb-6 max-w-2xl">
          <h2 className="text-3xl font-semibold tracking-tight">Crash it, then resume</h2>
          <p className="mt-3 text-mist">
            Run the three steps, or stop the process after analyze. Resume reads the
            journal and only runs what never finished.
          </p>
        </div>
        <WorkflowDemo />
      </section>

      <section className="mx-auto grid max-w-6xl gap-4 px-5 py-16 md:grid-cols-3">
        {points.map((point) => (
          <article key={point.title} className="rounded-3xl border border-white/10 bg-navy/60 p-6">
            <h2 className="text-xl font-semibold">{point.title}</h2>
            <p className="mt-3 text-sm leading-6 text-mist">{point.body}</p>
          </article>
        ))}
      </section>
    </>
  );
}
