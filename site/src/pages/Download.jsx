import { REPO, ZIP } from "../links.js";

const steps = [
  {
    title: "Download the install package",
    body: "The button saves Agen-0.1.0-win64.zip only. It does not download the source tree.",
  },
  {
    title: "Unzip it",
    body: "You get the Windows installer, the editor extension, examples, and the getting-started guide.",
  },
  {
    title: "Install it",
    body: "In the unzipped folder, run powershell -ExecutionPolicy Bypass -File .\\install.ps1. Then reload Cursor or VS Code.",
  },
];

export default function Download() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <p className="text-sm font-medium uppercase tracking-[0.2em] text-lilac">Download</p>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight">Download the install package</h1>
      <p className="mt-4 text-lg leading-8 text-mist">
        This is the Windows package for 64-bit Python 3.11. The link is the zip
        in the repository, not a copy of the codebase.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        <a
          href={ZIP}
          className="rounded-full bg-violet px-5 py-3 text-sm font-semibold text-white"
        >
          Download Agen zip
        </a>
        <a
          href={REPO}
          className="rounded-full border border-white/15 px-5 py-3 text-sm font-semibold"
        >
          View the repository
        </a>
      </div>
      <p className="mt-4 break-all text-sm text-mist">{ZIP}</p>
      <ol className="mt-12 space-y-4">
        {steps.map((step, index) => (
          <li key={step.title} className="rounded-3xl border border-white/10 bg-navy/60 p-6">
            <p className="text-sm text-lilac">Step {index + 1}</p>
            <h2 className="mt-1 text-xl font-semibold">{step.title}</h2>
            <p className="mt-2 text-mist">{step.body}</p>
          </li>
        ))}
      </ol>
    </article>
  );
}
