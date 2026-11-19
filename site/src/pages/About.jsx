const facts = [
  {
    title: "One file for the program and the deploy",
    body: "A resource and a deploy live next to the workflow. The runtime is the thing you install, not a second project you keep in sync.",
  },
  {
    title: "Failures are part of the language",
    body: "A step can retry a timeout, an HTTP 429, or a 5xx, then fall back. A workflow checkpoints. A service restarts, then alerts.",
  },
  {
    title: "Secrets stay out of source",
    body: "Names come from the environment or a vault reference. The values never belong in the program.",
  },
];

export default function About() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <p className="text-sm font-medium uppercase tracking-[0.2em] text-lilac">About</p>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight">Built for work that must finish</h1>
      <p className="mt-5 text-lg leading-8 text-mist">
        Agen is a language for agentic AI, automation, and cloud deployment.
        Josephat Oyondi is building it so a developer can describe what the
        workflow should do, and the runtime keeps that workflow running.
      </p>
      <div className="mt-10 space-y-4">
        {facts.map((fact) => (
          <section key={fact.title} className="rounded-3xl border border-white/10 bg-navy/60 p-6">
            <h2 className="text-xl font-semibold">{fact.title}</h2>
            <p className="mt-2 leading-7 text-mist">{fact.body}</p>
          </section>
        ))}
      </div>
      <p className="mt-10 leading-7 text-mist">
        The long-term goal is simple: when an agent or an automation has to keep
        running, Agen is the language people reach for. Speed is part of that
        promise. Independent calls can run together, and pure functions can run
        on the native virtual machine.
      </p>
    </article>
  );
}
