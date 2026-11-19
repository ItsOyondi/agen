import { useState } from "react";
import { ISSUES, REPO } from "../links.js";

const empty = { name: "", email: "", message: "" };

export default function Contact() {
  const [form, setForm] = useState(empty);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(null);

  function update(event) {
    setReady(null);
    setError("");
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
  }

  function submit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.email.trim() || !form.message.trim()) {
      setError("Name, email, and message are required.");
      return;
    }
    if (!form.email.includes("@")) {
      setError("Enter an email address that includes @.");
      return;
    }
    const title = `Note from ${form.name.trim()}`;
    const body = `${form.message.trim()}\n\nFrom: ${form.name.trim()} <${form.email.trim()}>`;
    const url = `${ISSUES}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
    setReady(url);
  }

  return (
    <article className="mx-auto grid max-w-5xl gap-10 px-5 py-16 lg:grid-cols-[0.8fr_1.2fr]">
      <div>
        <p className="text-sm font-medium uppercase tracking-[0.2em] text-lilac">Contact</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight">Write to the project</h1>
        <p className="mt-4 leading-7 text-mist">
          There is no mail server on this site. The form prepares a GitHub issue
          with your note so it reaches the Agen repository.
        </p>
        <a className="mt-6 inline-block text-lilac hover:text-white" href={REPO}>
          {REPO}
        </a>
      </div>
      <form onSubmit={submit} className="rounded-3xl border border-white/10 bg-navy/70 p-6" noValidate>
        <Field label="Name" name="name" value={form.name} onChange={update} autoComplete="name" />
        <Field
          label="Email"
          name="email"
          type="email"
          value={form.email}
          onChange={update}
          autoComplete="email"
        />
        <label className="mt-4 block text-sm font-medium" htmlFor="message">
          Message
        </label>
        <textarea
          id="message"
          name="message"
          rows={6}
          value={form.message}
          onChange={update}
          className="mt-2 w-full rounded-2xl border border-white/10 bg-ink px-4 py-3 outline-none focus:border-violet"
        />
        {error && <p className="mt-3 text-sm text-rose-300">{error}</p>}
        <button
          type="submit"
          className="mt-5 rounded-full bg-violet px-5 py-3 text-sm font-semibold text-white"
        >
          Prepare GitHub issue
        </button>
        {ready && (
          <p className="mt-4 text-sm leading-6 text-mist">
            Your note is ready.{" "}
            <a className="text-lilac hover:text-white" href={ready} target="_blank" rel="noreferrer">
              Open it on GitHub
            </a>{" "}
            to send it. GitHub will ask you to sign in.
          </p>
        )}
      </form>
    </article>
  );
}

function Field({ label, name, type = "text", ...props }) {
  return (
    <div className="mt-4 first:mt-0">
      <label className="block text-sm font-medium" htmlFor={name}>
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        {...props}
        className="mt-2 w-full rounded-2xl border border-white/10 bg-ink px-4 py-3 outline-none focus:border-violet"
      />
    </div>
  );
}
