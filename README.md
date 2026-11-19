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
| rewriting hot loops in C or Cython       | pure functions run on a built-in C++ VM automatically      |

## Requirements for a first program

You can write and run Agen with only Python. A cloud account, an API key, and extra packages are not required for the examples.

| Requirement | What you need |
|-------------|----------------|
| Python | 3.11, 64-bit, the same version the install package was built for. Check with `python --version`. |
| Agen itself | The Agen install package (`dist/Agen-0.1.0-win64.zip`). It has no third-party dependencies. |
| An editor | Cursor or VS Code. The installer sets up `.agn` icons and colors. Any editor can still open the files. |
| Optional | An OpenAI or Azure OpenAI key, only if an `agent` sets `provider` to `"openai"` or `"azure"`. The default `"mock"` provider works offline. |
| Optional | A TypeSafe key (`TYPESAFE_API_KEY`), only if the program calls `jev` or `jev_all`. |
| Optional | Azure CLI (`az login`) and a Key Vault, only if the program uses `KeyVaultCreds` instead of a `.env` file. |
| Optional | Nothing extra for the fast backend. It is already compiled into the install package. |

Install Agen from the package, not from the source tree. Download `dist/Agen-0.1.0-win64.zip`, unzip it, and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
agen --version      # agen 0.1.0 (native backend: on (C++ VM))
```

The installer puts `agen` on your user PATH and installs the editor support for Cursor and VS Code. Reload the editor afterwards (**Developer: Reload Window**). Build that zip from a checkout with `python release/build_release.py`. The zip contains the compiled program and the editor extension. It does not contain the Python or C++ source.

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
agen run    examples/hello.agn
agen run    examples/calculator.agn              # classes, methods, inheritance
agen run    examples/use_library.agn             # @at library @import names
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
fn add(a, b) { return a + b }   # function add(a, b) { ... } is the same
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

### Libraries

Other developers extend Agen with `.agn` libraries. Import them with `@`, not with Python's `from ... import ...`.

```agen
@import mathx                         # the whole library, used as mathx.add
@at mathx @import add, Counter        # selected functions and classes
@at "lib/mathx" @import add           # a path, in quotes
@imported                             # list the imports written in this file
```

A library is an `.agn` file. `mathx` resolves to `mathx.agn` beside the program, then `lib/mathx.agn`, then each directory in `AGEN_LIB`. `lib.mathx` is the same file as `lib/mathx`. Names cannot contain `..`.

Typing `@imported` in the editor opens a popup of every `@import` and `@at ... @import` in that file. Running it prints the same list. Hover `@imported` to see the list without running. See `examples/use_library.agn`.

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

Threads always overlap waiting. Computation runs in parallel only for
functions on the C++ backend (below). Other Agen code runs one thread at a
time.

### C++ backend

Pure functions run on a built-in bytecode virtual machine. The install package already includes it, so a program does not change to use it. A function is compiled the
first time it is called if it only uses:

* numbers, strings, booleans, `null`, and lists
* `let`, assignment, `if`, `while`, `for`, and `return`
* `len`, `range`, `int`, `float`, `str`, `round`, and other functions that qualify

Anything else stays on the Python interpreter: maps, classes, agents,
`print`, I/O, steps, and global variables. A common pattern is to keep
workflows and agents in Agen's normal code and move the number crunching into
small pure functions.

Results are identical to the Python interpreter. The VM never guesses at
Python behavior. On an error, a 64-bit integer overflow (`factorial(30)`), or
a value it can't format exactly (a float turned into text), it stops, and the
interpreter reruns that call. A rerun is safe because these functions have no
side effects. The tests run every program on both backends and compare the results.

The VM releases Python's global interpreter lock (GIL) while it runs, so
`pmap(fn, items)` over a pure function uses several CPU cores. Python threads
can't do this.

Measured with `python benchmarks/compare.py` (best of 7, 12-core Windows laptop):

| workload | CPython 3.11 | Agen with C++ | Agen on Python |
|----------|-------------:|--------------:|---------------:|
| `fib(27)` | 0.032s | 0.021s (1.5x faster) | 16.5s |
| `count_primes(200000)` | 0.406s | 0.288s (1.4x faster) | 182.7s |
| `sum_squares(3000000)` | 0.208s | 0.097s (2.2x faster) | 57.8s |
| string building, 200000 appends | 0.016s | 0.003s (5x faster) | 0.97s |
| 4 x `count_primes(150000)` on 4 threads | 1.059s | 0.278s (3.8x faster) | |

Timings vary with machine load. Run the script to measure your own machine.
`examples/native_speed.agn` shows the same workloads as a program.

To turn the backend off, use `agen run --no-native` or set `AGEN_NATIVE=0`.
`agen --version` shows whether it is active.

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
| secrets        | `keyvault(name)`, class `KeyVaultCreds` |

## Agents and providers

`ask` uses the agent's `provider`:

* `mock` (default): deterministic, offline. Good for tests and demos.
* `openai`: needs `OPENAI_API_KEY`; optional `OPENAI_BASE_URL`; uses `model`.
* `azure`: needs `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`; optional `AZURE_OPENAI_API_VERSION`; uses `deployment` or `model`.

Set `cache = true` in an agent to reuse the answer for an identical prompt within one run.

Secrets are never written in a program. `.env` is always loaded. Key Vault is optional; see [Secret store](#secret-store).

## Secret store

`KeyVaultCreds` is optional. A program that never calls it does not need the Azure CLI, and `agen run` still loads `.env`. Azure is contacted only by `get`, `names`, `creds`, and `load`. Secret values stay in memory for that run: `print` shows `secret("NAME")`, and a workflow journal stores the name so a resume fetches the secret again instead of keeping a copy.

```agen
let creds = KeyVaultCreds("my-agen-vault")
let key = creds.get("AZURE-OPENAI-KEY")   # prints as secret("AZURE-OPENAI-KEY")
print(creds.creds())                        # names only
creds.load()                                # installs them for ask() and jev()
```

`AZURE-OPENAI-KEY` is also installed as `AZURE_OPENAI_KEY`, which is the name the OpenAI and TypeSafe providers already use. A name with underscores is tried, then the same name with hyphens, because Key Vault names cannot contain `_`.

To use this for a project, sign in once with `az login`, then put the vault name beside the program:

```json
{ "vault": "my-agen-vault" }
```

Save that as `vault.json` (it is gitignored). It only stores the vault name, so `KeyVaultCreds(env("AGEN_KEYVAULT_NAME"))` can find it. Copy `projects/vault.json.example` and see `projects/keyvault_creds.agn`. Call `creds.load()` in the program that needs the secrets.

The access token from `az account get-access-token` is sent only to `https://<vault>.vault.azure.net`. Store a secret without putting the value in the command history:

```powershell
az keyvault secret set --vault-name my-agen-vault --name AZURE-OPENAI-KEY --file .\openai.key
```

Do not `print(env("AZURE_OPENAI_KEY"))` after `load()`. That reads the raw string back out of the environment.

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

The installer adds Agen syntax, the Agen file icon, and the Seti (Agen) icon theme to Cursor and VS Code. Reload the window afterwards (**Developer: Reload Window**).

Keywords are split into colors. A new keyword is added to one of these groups in `editors/vscode/syntaxes/agen.tmLanguage.json`. The color is set in `.vscode/settings.json` and in the extension's `configurationDefaults`, so an installed copy has the same colors:

| Group | Color | Examples |
|-------|-------|----------|
| Control flow | purple `#C586C0` | `if` `else` `while` `for` `return` |
| Imports | red `#E06C75` | `@import` `@at` `@imported` |
| Declarations | blue `#569CD6` | `let` `fn` `function` `class` `extends` `init` |
| Function names | cyan `#56B6C2` | `fn greet`, `function greet`, and calls such as `greet(...)` or `counter.count_primes(...)` |
| Class names | violet `#C792EA` | `class Counter`, `extends Parent`, and uses such as `PrimeCounter()` |
| Operators | gold `#D7BA7D` | `and` `or` `not` `in` `with` |
| Agentic | teal `#4EC9B0` | `workflow` `step` `agent` `deploy` `parallel` |
| Built-ins | yellow `#DCDCAA` | `print` `ask` `jev` `pmap` |
| Invalid Python | red underline `#F44747` | `from x import y`, `import x` |

`self` and `super` are light blue. `true`, `false`, `null`, and numbers are green. Strings are orange. Comments are muted green, whether they start with `#`, sit between `"""` and `"""`, or sit between `/*` and `*/` (including `/** ... */`). The scope for `print` is `support.function.agen`. The scope for `@import` is `keyword.control.import.agen`. A defined function name is `entity.name.function.agen`. A defined class name is `entity.name.class.agen`.

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
  imports.py                        @import and @at library resolution
  secrets.py                        Azure Key Vault secret store (values stay out of journals)
  native.py                         compiles pure functions to bytecode for the C++ VM
native/vm.cpp                       C++ bytecode VM (built as agen._native)
benchmarks/compare.py               CPython vs Agen timings
  events.py                         event log and alerting
  builtins.py  cli.py
tests/                              python -m unittest discover -s tests
```

## Roadmap ideas

Real cloud providers for `deploy` (Azure SDK / Terraform runner), maps and
classes on the C++ backend, agent tool-calling, circuit breakers shared across runs, and a
distributed journal (e.g. Cosmos DB) so workflows can resume on another machine.

## License

Copyright (c) 2026 Josephat Oyondi.

Agen is released under the MIT License. See [LICENSE](LICENSE) for the full text.
