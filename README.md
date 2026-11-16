# Agen

**Agen** is a small language for agentic AI, automation and cloud deployment.
Infrastructure, deployment and application logic live in the same file, and
fault tolerance is built into the language instead of written by hand.

In Python, a production workflow needs `try/except`, retry loops, backoff,
timeouts, checkpointing, supervisors and alerting, all written by hand. In
Agen they are part of the language:

| Python (you write it)                    | Agen (the runtime does it)                                 |
|------------------------------------------|------------------------------------------------------------|
| retry loops with backoff                 | `step` retries automatically (exponential backoff + jitter) |
| timeouts                                 | `step x with {timeout: 5} { ... }`                         |
| graceful degradation                     | `step ... fallback { ... }`                                |
| checkpoint / resume after crash          | every `workflow` is durable and resumes from the last step |
| process supervisors                      | `service` restarts on crash, then alerts                   |
| monitoring and alerting                  | structured event log + alerts (console and webhook)        |
| separate Terraform / pipeline files      | `resource` + `deploy` in the same program                  |
| thread pools, futures, connection pools  | `parallel { ... }`, `pmap`, `ask_all`, `jev_all`           |
| an SDK per model provider                | `jev(text, questions)` for typed answers from TypeSafe JEV |

## Requirements for a first program

You can write and run Agen with only Python. A cloud account, an API key, and extra packages are not required for the examples.

| Requirement | What you need |
|-------------|----------------|
| Python | 3.10 or newer. Check with `python --version`. |
| Agen itself | This folder. It has no third-party dependencies. |
| An editor | Any text editor. Save programs with the `.agn` extension. |
| Optional | An OpenAI or Azure OpenAI key, only if an `agent` sets `provider` to `"openai"` or `"azure"`. The default `"mock"` provider works offline. |
| Optional | A TypeSafe key (`TYPESAFE_API_KEY`), only if the program calls `jev` or `jev_all`. |

Install the `agen` command once, from this folder:

```powershell
cd agen
pip install -e .
agen --version
```

If `python` is not on your PATH, use the project virtual environment from the workspace root instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -e agen
.\.venv\Scripts\python.exe -m agen --version
```

Create a file such as `hello.agn`:

```agen
# Statements end at a newline. Comments start with #.
fn greet(name) {
    return "Hello, " + name
}

print(greet("Agen"))
```

Run it, or check the syntax without running it:

```powershell
agen run hello.agn
agen check hello.agn
```

`agen run` prints your `print` output, then a short summary of steps, retries, and alerts.

A few rules that stop a first program from running:

* One statement per line, or separate statements with `;`. Blocks use `{` and `}`.
* Strings use double quotes: `"hello"`.
* `step` belongs inside a `workflow`. `self` belongs inside a method.
* `put` and `get` read the environment created by `deploy`. Call them from inside a `deploy` block.
* Running the same workflow again returns its saved result. Add `--fresh` to start over: `agen run hello.agn --fresh`.
* A finished run leaves a `.agen` folder next to the program (checkpoints and logs). It is safe to delete.

Copy `examples/hello.agn` for the basics, `examples/calculator.agn` for classes, and `examples/report_pipeline.agn` for an agent plus a deployment.

## Quick start

No dependencies, Python 3.10+.

```powershell
cd agen
pip install -e .            # installs the `agen` command (or use: $env:PYTHONPATH="src"; python -m agen ...)

agen run    examples/hello.agn
agen run    examples/calculator.agn              # classes, methods, inheritance
agen plan   examples/report_pipeline.agn     # preview infrastructure changes
agen run    examples/report_pipeline.agn     # deploy, then run the workflow
agen status examples/report_pipeline.agn     # checkpoints and deployed environments
agen export examples/report_pipeline.agn -o main.tf.json   # Terraform for Azure
```

See crash recovery in action:

```powershell
agen run examples/agent_triage.agn --crash-after "classify#1"   # process dies mid-batch
agen run examples/agent_triage.agn                              # resumes at ticket 3
```

## A complete program

```agen
resource group rg {
    location = "eastus"
}

resource storage reports {
    resource_group_name = rg          # references define dependencies
    account_tier        = "Standard"
}

agent analyst {
    provider     = env("AGEN_LLM_PROVIDER", "mock")    # mock | openai | azure
    instructions = "You are a concise financial analyst."
}

workflow daily_report(day) {
    step fetch with {retries: 4, backoff: 0.1} {
        prices = json_parse(http_get("https://example.com/prices/" + day))
    }
    step analyze with {timeout: 30} {
        summary = ask(analyst, "Summarize: " + str(prices))
    } fallback {
        summary = "analysis unavailable"
    }
    step publish {
        put(reports, day, {prices: prices, summary: summary})
    }
    return summary
}

deploy dev {
    service reporter with {restarts: 2} {
        print(daily_report("2026-11-16"))
    }
}
```

## Language tour

```agen
let x = 10                     # declare (plain `x = 10` also works)
fn add(a, b) { return a + b }
if x > 5 { print("big") } else { print("small") }
for item in [1, 2, 3] { print(item) }
while x > 0 { x = x - 1 }
let m = {name: "agen", tags: ["ai", "cloud"]}
print(m.name, m["tags"][0])
```

* Types: number, string, bool, `null`, list, map, plus fn, class, workflow, resource and agent.
* Operators: `+ - * / %`, `== != < <= > >=`, `and or not`. `string + anything` concatenates.
* Scoping: assignment binds in the current function/workflow scope; reads see enclosing scopes.
* Statements are separated by newlines or `;`.

### Classes

```agen
class Number {
    init(value) { self.value = value }          # constructor
    fn add(amount) { return Number(self.value + amount) }
}
class RunningTotal extends Number {
    init(value) {
        super(value)                            # parent constructor
        self.adds = 0
    }
    fn add(amount) {
        self.adds = self.adds + 1
        self.value = super.add(amount).value    # parent method
        return self.value
    }
}
```

`self` is the current instance. `super(args)` calls the parent constructor, and
`super.method(args)` calls a parent method. Fields are created by assigning
them. Instances can be stored in a workflow step: the journal keeps their
fields and rebuilds the object on resume. See `examples/calculator.agn`.

### Fault-tolerance constructs

**`workflow name(params) { ... }`**: a durable function. The run id is derived
from the workflow name, its arguments, and the program text, so calling it
again with the same arguments resumes the same run. A finished run returns its
saved result. Editing the program, such as changing a prompt, starts a new run.
Use `agen run --fresh` to rerun the same program from scratch.

**`step name [with {options}] { ... } [fallback { ... }]`**: the unit of
durability, used inside workflows. Options:

| option        | default | meaning                                        |
|---------------|---------|------------------------------------------------|
| `retries`     | 2       | extra attempts after the first failure         |
| `backoff`     | 0.2     | base delay in seconds (doubles each attempt)   |
| `max_backoff` | 10      | cap on the delay                               |
| `timeout`     | none    | seconds per attempt                            |
| `jitter`      | true    | randomize delays to avoid thundering herds     |

If an attempt fails, its variable changes are rolled back before the next try.
When a step succeeds, the variables it wrote are journaled. On resume,
completed steps are skipped and those variables are restored. If every retry
fails, the `fallback` block runs. If there is no fallback, the step fails, an
alert is raised and the error propagates.

**`service name [with {restarts: 3, backoff: 0.5, timeout: ...}] { ... }`**: a
supervised component. If it crashes, it restarts with backoff. Because the
workflows it calls are durable, a restart continues where the previous attempt
stopped. If it stays down, an alert is raised.

**`deploy env [with {provider: "local", retries: 3}] { ... }`**: reconciles
every declared `resource` for that environment (create / update / delete,
like `terraform apply`), checkpointing each change, then runs the body with
that environment active.

### Error classification

The runtime only retries failures that can heal on their own:

* **Retried:** network errors, HTTP 429/5xx, timeouts, `fail()`, `chaos()`, provider outages.
* **Fail fast:** syntax errors, unknown names, type errors, bad indexes, HTTP 4xx.

### Determinism rule

Workflows replay their control flow on resume. Put anything
non-deterministic (network calls, `ask`, `now()`, `chaos()`) **inside a
step** so that its result is journaled. Steps inside loops get stable keys
(`classify`, `classify#1`, ...), and the same rule keeps those keys stable.

## Speed: parallelism and JEV

Agent and cloud programs spend most of their time waiting on the network.
Agen speeds up that waiting in four ways, with no extra code:

1. **Parallel execution.** Independent model and HTTP calls run at the same time.
2. **One request, many questions.** `jev` asks every typed question about an input in a single call.
3. **Connection reuse.** HTTP connections are kept alive and pooled, so later calls skip the TCP and TLS handshakes.
4. **Caching.** With `cache: true`, an identical model request in the same run is answered from memory.

Measured on the TypeSafe API: 6 `jev` requests took 1.64s one at a time and 0.32s with `jev_all`.

**`parallel [with {workers: n}] { ... }`** runs each statement of the block at
the same time, then continues when all of them are done. Each statement gets
its own scope, and the variables it assigns are visible after the block. Two
statements that assign the same name are an error.

```agen
parallel {
    prices = http_get("https://example.com/prices")
    news   = http_get("https://example.com/news")
    sentiment = ask(analyst, "Market mood today?")
}
print(len(prices), len(news), sentiment)
```

**Lists in parallel.** Results keep the input order. The default is 8 workers.

| function | what it does |
|----------|--------------|
| `pmap(fn, items, workers)` | calls `fn(item)` for every item at the same time |
| `ask_all(agent, prompts, workers)` | `ask` for every prompt at the same time |
| `jev_all(inputs, questions, options)` | `jev` for every input at the same time |

Every task finishes before the first error (in input order) is raised. A
`step` inside a parallel task gets a key such as `pmap0.2.load`, so a resumed
workflow replays the branches that finished and reruns only the rest.

Threads overlap waiting, not computation. Pure Agen code (loops and arithmetic)
runs one thread at a time and gets no faster in `parallel`.

### JEV typed questions

JEV is the model behind [TypeSafe AI](https://docs.typesafe.ai/). Instead of
returning text, it answers typed questions with probabilities, so a program can
branch on confidence:

| constructor | answer fields |
|-------------|---------------|
| `choice(instructions, {label: description, ...})` | `choice`, `confidence`, `probabilities` |
| `score(instructions, [level0, level1, ...])` | `score` (0 = first level), `confidence`, `legend`, `probabilities` |
| `noul(instructions)` | `noul`, the probability that the statement is true |

```agen
let questions = {
    department: choice("Which team should handle this", {
        billing: "Payment or subscription issues",
        technical: "Bugs or integration problems"
    }),
    urgent: noul("The message conveys urgency")
}
let a = jev("My Stripe integration keeps failing. Please help ASAP.", questions)
if a.urgent.noul >= 0.7 { print("page on-call: " + a.department.choice) }
```

`jev(input, questions, options)` accepts text or a map as the input. Options
are `model` (default `jev-latest`), `timeout`, `cache`, and `workers` (for
`jev_all`). Put calls inside a `step` so retries and replay apply. See
`projects/jev_tickets.agn`.

**Where the key goes:** put `TYPESAFE_API_KEY=...` in a `.env` file next to the
program (for example `projects/.env`) or set it in the environment. `agen run`
loads that `.env` automatically. `TYPESAFE_BASE_URL` and
`TYPESAFE_DEFAULT_MODEL` are optional overrides. The key is sent only in the
`Authorization` header and never appears in logs or journals.

## Built-in functions

| area           | functions |
|----------------|-----------|
| core           | `print len str int float round range push keys join split contains upper lower type` |
| time / env     | `sleep now timestamp env` |
| data / network | `json_parse json_str http_get http_post` |
| agents         | `ask(agent, prompt)`, `ask_all(agent, prompts, workers)` |
| parallelism    | `pmap(fn, items, workers)` |
| JEV (TypeSafe) | `jev(input, questions, options)`, `jev_all(inputs, questions, options)`, `choice`, `score`, `noul` |
| cloud storage  | `put(resource, key, value)`, `get(resource, key, default)` |
| observability  | `emit(name, data)`, `alert(message)` |
| fault testing  | `fail(msg)`, `chaos(probability)`, `flaky(name, n)` (fails the first n calls) |

## Agents and providers

`ask` uses the agent's `provider`:

* `mock` (default): deterministic, offline. Good for tests and demos.
* `openai`: needs `OPENAI_API_KEY`; optional `OPENAI_BASE_URL`; uses `model`.
* `azure`: needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`; optional `AZURE_OPENAI_API_VERSION`; uses `deployment` or `model`.

Set `cache = true` in an agent to reuse the answer for an identical prompt within one run.

Secrets are read only from environment variables, never from source code.

## Monitoring

* Every event (step start/retry/success, replays, restarts, deploy changes) is
  appended as JSON lines to `.agen/events.jsonl`, ready to ship to a log pipeline.
* Alerts go to stderr and, if `AGEN_ALERT_WEBHOOK` is set, are POSTed as JSON
  (Slack/Teams-compatible `text` field).
* Each run ends with a summary line: steps run, replayed, retried, recovered, failed.

## State layout

```
.agen/
  runs/<workflow>-<hash>.json   workflow journals (atomic writes)
  infra/<env>.json              deployed resource state per environment
  cloud/<env>/<resource>/       local provider's simulated cloud (storage data)
  events.jsonl                  structured event log
```

## Syntax highlighting

Editors color keywords only when they have a grammar for the file type. This
repo includes one for `.agn` files at `editors/vscode`. It is installed for
Cursor by copying that folder to:

```
%USERPROFILE%\.cursor\extensions\agen-lang.agen-0.1.0
```

Reload the window afterwards (**Developer: Reload Window**). The extension
gives every `.agn` file its own icon in the explorer, the same way Python
files show the Python icon. It selects the **Seti (Agen)** icon theme, which
is the normal Seti theme plus the Agen mark. For VS Code, copy the same folder
to `%USERPROFILE%\.vscode\extensions\agen-lang.agen-0.2.0`.

Keywords are split into colors: control flow is purple, declarations are blue,
`and`/`or`/`not` are gold, and `workflow`, `step`, `agent`, `deploy`, and
`parallel` are teal. Built-ins such as `print` and `ask` are yellow. Change a color in
`.vscode/settings.json` under `editor.tokenColorCustomizations`. The scope for
`print` is `support.function.agen`.

## Project layout

```
src/agen/
  lexer.py  parser.py  nodes.py     syntax
  interpreter.py                    evaluation + durable step/service/deploy semantics
  values.py                         runtime types and operators
  durable.py                        journal, encoding, atomic writes
  resilience.py                     retry policy (backoff, jitter, timeouts)
  infra.py  terraform.py            plan/apply, local provider, Terraform export
  agents.py  jev.py                 LLM providers, JEV typed questions (TypeSafe)
  net.py                            pooled keep-alive HTTP with fault classification
  events.py                         event log and alerting
  builtins.py  cli.py
tests/                              python -m unittest discover -s tests
```

## Roadmap ideas

Real cloud providers for `deploy` (Azure SDK / Terraform runner), process
pools for CPU-bound work, agent tool-calling, circuit breakers shared across runs, and a
distributed journal (e.g. Cosmos DB) so workflows can resume on another machine.

## License

Copyright (c) 2026 Josephat Oyondi.

Agen is released under the MIT License. See [LICENSE](LICENSE) for the full text.
