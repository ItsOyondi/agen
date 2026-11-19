"""Build the first-user Agen tutorial PDF. Not part of the language runtime."""

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

OUT = Path(__file__).resolve().parent / "Agen-Getting-Started.pdf"
LOGO = Path(__file__).resolve().parent / "agen-logo.png"
MARK = Path(__file__).resolve().parent / "agen-mark.png"


class Guide(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.image(str(MARK), x=self.l_margin, y=8, w=6.5)
        self.set_xy(self.l_margin + 8.5, 8)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(90, 98, 110)
        self.cell(140, 6.5, "Agen Getting Started")
        self.cell(0, 6.5, str(self.page_no()), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(220, 224, 230)
        y = self.get_y()
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(4)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(130, 136, 146)
        self.cell(0, 8, "Agen 0.1.0   |   For first-time users", align="C")

    def h1(self, text):
        self.ln(2)
        if self.get_y() > 240:
            self.add_page()
        self.start_section(text)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(22, 42, 74)
        self.multi_cell(0, 8, text)
        self.set_draw_color(47, 111, 178)
        y = self.get_y()
        self.line(self.l_margin, y, self.l_margin + 28, y)
        self.ln(4)

    def h2(self, text):
        self.ln(1)
        if self.get_y() > 250:
            self.add_page()
        self.start_section(text, level=1)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(30, 64, 110)
        self.multi_cell(0, 7, text)
        self.ln(1)

    def body(self, text):
        self.set_font("Helvetica", "", 11)
        self.set_text_color(33, 37, 41)
        self.multi_cell(0, 5.6, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 11)
        self.set_text_color(33, 37, 41)
        x = self.l_margin
        self.set_x(x)
        self.cell(6, 5.6, "-")
        self.multi_cell(0, 5.6, text)
        self.ln(0.6)

    def code(self, text):
        lines = text.strip("\n").split("\n")
        line_h = 4.5
        block_h = len(lines) * line_h + 5
        if self.get_y() + block_h > self.page_break_trigger:
            self.add_page()
        self.set_fill_color(244, 246, 248)
        self.set_text_color(28, 36, 48)
        self.set_font("Courier", "", 9)
        x = self.l_margin
        y = self.get_y()
        width = self.w - self.l_margin - self.r_margin
        self.rect(x, y, width, block_h, style="F")
        self.set_xy(x + 2.5, y + 2.2)
        for line in lines:
            self.cell(width - 5, line_h, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_x(x + 2.5)
        self.set_y(y + block_h + 2)
        self.set_text_color(33, 37, 41)

    def table(self, headers, rows, widths):
        row_h = 7
        needed = row_h * (len(rows) + 1) + 2
        if self.get_y() + needed > self.page_break_trigger:
            self.add_page()
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(22, 42, 74)
        self.set_text_color(255, 255, 255)
        for header, width in zip(headers, widths):
            self.cell(width, row_h, "  " + header, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 9)
        self.set_text_color(33, 37, 41)
        for index, row in enumerate(rows):
            self.set_fill_color(244, 246, 248 if index % 2 == 0 else 255)
            if index % 2 == 0:
                self.set_fill_color(244, 246, 248)
            else:
                self.set_fill_color(255, 255, 255)
            for value, width in zip(row, widths):
                self.cell(width, row_h, "  " + value, fill=True)
            self.ln()
        self.ln(3)


def build():
    pdf = Guide(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 16, 18)
    pdf.set_title("Agen Getting Started")
    pdf.set_author("Josephat Oyondi")
    pdf.set_creator("Agen")

    pdf.add_page()
    pdf.set_fill_color(22, 42, 74)
    pdf.rect(0, 0, 216, 128, style="F")
    pdf.image(str(LOGO), x=18, y=16, w=86)
    pdf.set_xy(18, 64)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 28)
    pdf.multi_cell(160, 12, "Getting started", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(198, 216, 236)
    pdf.multi_cell(170, 6.5, "Install Agen, write a first program, and learn the pieces\nevery new user needs.  Version 0.1.0.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(18)
    pdf.multi_cell(170, 6.5, "You write what the workflow should do. Agen keeps it running.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_y(146)
    pdf.set_text_color(33, 37, 41)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Who this is for", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, "Someone who has the Agen install package and wants to run programs. You do not need the source code, a cloud account, or an API key to finish the first half of this guide.")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "What you will be able to do", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for item in (
        "Install the agen command and the editor support.",
        "Write and run a program with functions, lists, and decisions.",
        "Use classes and share code with a library.",
        "Write a workflow that retries, remembers progress, and resumes after a crash.",
        "Call an agent, ask typed questions, and run independent work at the same time.",
        "Keep secrets out of the program.",
    ):
        pdf.bullet(item)

    pdf.add_page()
    pdf.h1("1. What Agen is")
    pdf.body("Agen is a language for agentic AI, automation, and cloud deployment. The program, the retries, the saved progress, and the infrastructure live in one .agn file.")
    pdf.h2("The problem")
    pdf.body("A workflow that calls a model, an API, and a cloud service is not finished when the happy path works. The same run has to survive a timeout, a rate limit, and a process that dies halfway through. A restart must not repeat a charge, resend an email, or call the model again for a step that already succeeded.")
    pdf.body("Developers usually write that protection by hand: retry loops, backoff, checkpoint files, process supervisors, alerts, and a separate deployment project. That code is often larger than the business logic. A mistake in it stays quiet until production, which is the worst time to discover that a finished step ran twice.")
    pdf.h2("The objective")
    pdf.body("Agen's objective is to make that reliability part of the language. You describe the work. The runtime retries what can heal on its own, remembers what already finished, restarts what crashed, and raises an alert when it cannot recover. The resources the program needs are declared beside the code that uses them, so you read one file instead of an application, a pipeline, and a Terraform project.")
    pdf.bullet("A step retries transient failures, such as network errors, HTTP 429 and 5xx, and timeouts. A fallback block runs if every retry fails.")
    pdf.bullet("A workflow saves each finished step. Run the same file after a crash and it resumes. Finished work is not repeated.")
    pdf.bullet("A service restarts a crashed process. Because the workflow is durable, the restart continues from the last finished step.")
    pdf.bullet("resource and deploy create what the program needs, then run the program in that environment. A local deploy works with no cloud account.")
    pdf.bullet("Secrets stay out of the source. The program names a key. It does not embed the value, and print does not reveal it.")
    pdf.h2("The long-term goal")
    pdf.body("The long-term goal is that Agen is the language developers choose when an agent or an automation has to keep running. Shipping that kind of program should mean writing the workflow, not assembling retries, journals, supervisors, and deployment from a stack of libraries.")
    pdf.body("Speed is part of the same goal. Reliability should not force the program to be slow. Independent model and HTTP calls run at the same time. Pure computation runs on the built-in fast engine. You do not rewrite the hot path in another language to get either property.")
    pdf.body("A plain program can still be a function and a print. You add a workflow when a crash must not throw work away, a step when a call may fail and should be tried again, and a deploy block when the program needs infrastructure before it runs.")
    pdf.table(
        ["You would otherwise write", "Agen already does"],
        [
            ["Retry loops and backoff", "step retries with backoff and jitter"],
            ["Timeouts", "step name with {timeout: 5}"],
            ["A backup path when a call fails", "step ... fallback { ... }"],
            ["Save progress and resume", "workflow checkpoints each step"],
            ["Restart a crashed process", "service restarts, then alerts"],
            ["A separate Terraform file", "resource and deploy in the same file"],
            ["Thread pools for many calls", "parallel, pmap, ask_all, jev_all"],
        ],
        [88, 92],
    )

    pdf.h1("2. What you need")
    pdf.body("The install package is Agen-0.1.0-win64.zip. Unzip it. You should see install.ps1, a package folder, an editor folder, an examples folder, and this kind of guide. That zip is the file to share. Do not send a source checkout.")
    pdf.table(
        ["Requirement", "You need"],
        [
            ["Windows", "64-bit Windows"],
            ["Python", "64-bit Python 3.11. Check with python --version"],
            ["Agen", "The unzipped install package"],
            ["Editor", "Cursor or VS Code. Any editor can still open .agn files"],
            ["Cloud or API keys", "Not required for the first programs"],
        ],
        [42, 138],
    )
    pdf.body("Keys are optional, and only for the feature that uses them:")
    pdf.bullet("An OpenAI or Azure OpenAI key, only when an agent sets provider to \"openai\" or \"azure\". The default provider, \"mock\", works offline.")
    pdf.bullet("A TypeSafe key named TYPESAFE_API_KEY, only when the program calls jev or jev_all.")
    pdf.bullet("The Azure CLI and a Key Vault, only when the program calls KeyVaultCreds. A program that never does this does not contact Azure.")
    pdf.body("The fast backend is already inside the package. You do not install a C++ compiler.")

    pdf.h1("3. Install")
    pdf.body("Open PowerShell in the unzipped folder and run:")
    pdf.code("powershell -ExecutionPolicy Bypass -File .\\install.ps1")
    pdf.body("The installer creates a private runtime for Agen, puts agen on your user PATH, and copies the editor support into Cursor and VS Code. It does not change other Python projects.")
    pdf.body("Close that terminal and open a new one so PATH is picked up. Then check the install:")
    pdf.code("agen --version")
    pdf.body("A successful install prints a line like this:")
    pdf.code("agen 0.1.0 (native backend: on (C++ VM))")
    pdf.body("\"native backend: on\" means the fast built-in engine is active. If Python is missing or is not 64-bit Python 3.11, the installer stops and tells you. Install Python from python.org, allow it to be added to PATH, and run install.ps1 again.")

    pdf.h1("4. The editor")
    pdf.body("In Cursor or VS Code, open the Command Palette and run Developer: Reload Window. After that, .agn files show the Agen icon, and the file tree uses the Seti (Agen) icon theme.")
    pdf.body("Colors tell you what a word is. You do not configure them yourself.")
    pdf.table(
        ["Color", "What it marks", "Examples"],
        [
            ["Purple", "Control flow", "if  else  while  for  return"],
            ["Red", "Imports", "@import  @at  @imported"],
            ["Blue", "Declarations", "let  fn  function  class  extends  init"],
            ["Cyan", "Your functions", "fn greet  function greet  and  greet(...)"],
            ["Violet", "Your classes", "class Counter  and  PrimeCounter()"],
            ["Gold", "Operators", "and  or  not  in  with"],
            ["Teal", "Agentic forms", "workflow  step  agent  deploy  parallel"],
            ["Yellow", "Built-ins", "print  ask  jev  pmap  range"],
        ],
        [28, 40, 112],
    )
    pdf.body("self and super are light blue. true, false, null, and numbers are green. Strings are orange. Comments are muted green. A comment starts with # and runs to the end of the line.")

    pdf.h1("5. Your first program")
    pdf.body("Create a folder for your own programs, and in it a file named hello.agn:")
    pdf.code(
        """# Statements end at a newline. Comments start with #.
fn greet(name) {
    return "Hello, " + name
}

print(greet("Agen"))"""
    )
    pdf.body("From that folder, run it:")
    pdf.code("agen run hello.agn")
    pdf.body("You should see the greeting, then a summary. A program with no workflow has nothing to checkpoint, so the counts are zero. That is expected.")
    pdf.code(
        """Hello, Agen
[agen] summary: 0 step(s) ran, 0 replayed, 0 retried, 0 recovered by fallback, 0 failed, ..."""
    )
    pdf.body("Check the syntax without running the program:")
    pdf.code("agen check hello.agn")
    pdf.body("That prints hello.agn: ok and the number of top-level statements.")
    pdf.h2("Rules that stop a first file from running")
    pdf.bullet("One statement per line. To put two statements on one line, separate them with a semicolon. Blocks use { and }.")
    pdf.bullet("Strings use double quotes: \"hello\". There is no single-quoted string.")
    pdf.bullet("A name must exist before you use it. greet on line 6 works because fn greet is above it.")
    pdf.bullet("step belongs inside a workflow. self belongs inside a method.")
    pdf.bullet("put and get talk to storage created by deploy. Call them from inside a deploy block.")

    pdf.h1("6. Values, names, and decisions")
    pdf.body("Agen has numbers, strings, booleans (true and false), null, lists, and maps. let names a value. A plain assignment such as x = 10 also works once the name exists in that scope.")
    pdf.code(
        """let x = 10
let ready = true
let team = ["ada", "grace", "linus"]
let config = {region: "eastus", replicas: 3}

print(team[0])
print(config.region)
print(config["replicas"])"""
    )
    pdf.body("A list is ordered and starts at index 0. A map is a set of named fields. Read a field with a dot, config.region, or with brackets, config[\"replicas\"].")
    pdf.h2("Operators")
    pdf.body("Arithmetic is + - * / %. Comparison is == != < <= > >=. Combine tests with and, or, and not. Adding a string to another value concatenates them, so \"total \" + 3 is the string \"total 3\".")
    pdf.h2("Decisions and loops")
    pdf.code(
        """if config.replicas > 1 {
    print("running", config.replicas, "replicas in", config.region)
} else {
    print("single instance")
}

for person in team {
    print(person)
}

while x > 0 {
    x = x - 1
}"""
    )
    pdf.body("for walks a list. while repeats until the test is false. Assignment updates the name in the current function or workflow. A read can see a name from an enclosing scope.")
    pdf.body("The examples folder in the install package includes hello.agn, which prints a greeting for each person on a team. Run it with:")
    pdf.code("agen run examples\\hello.agn")

    pdf.h1("7. Functions")
    pdf.body("fn or function names a function. Parameters are listed in parentheses. return sends a value back. A function with no return produces null.")
    pdf.code(
        """fn total(values) {
    let sum = 0
    for v in values {
        sum = sum + v
    }
    return sum
}

print(total([101.5, 99.2, 103.8]))"""
    )
    pdf.body("Call a function by writing its name and arguments: total(prices) or greet(\"Agen\"). A method call looks like counter.count_primes(200000). In the editor, those names are cyan. Built-ins such as print and len stay yellow.")
    pdf.body("A function that only uses numbers, strings, booleans, null, lists, let, assignment, if, while, for, return, and simple built-ins such as len, range, int, float, str, and round is run on the fast engine automatically. You do not mark it. Maps, classes, agents, print, network calls, and steps stay on the normal interpreter. Put heavy number work in a small pure function and call it from the rest of the program.")
    pdf.body("agen --version shows whether that engine is on. Turn it off for one run with agen run hello.agn --no-native, or set the environment variable AGEN_NATIVE=0. Results are the same either way.")

    pdf.h1("8. Classes")
    pdf.body("class groups data and methods. init is the constructor. self is the current instance. Assigning self.a creates the field. A class name starts with a capital letter, and the editor shows it in violet both at the definition and at the call, as in Calculator(2, 3).")
    pdf.code(
        """class Calculator {
    init(a, b) {
        self.a = a
        self.b = b
    }
    fn add() {
        return self.a + self.b
    }
}

let calc = Calculator(2, 3)
print(calc.add())"""
    )
    pdf.body("extends makes a child class. super(args) calls the parent constructor. super.method(args) calls a parent method. Both belong inside the class.")
    pdf.code(
        """class Number {
    init(value) { self.value = value }
    fn add(amount) { return Number(self.value + amount) }
}

class RunningTotal extends Number {
    init(value) {
        super(value)
        self.adds = 0
    }
    fn add(amount) {
        self.adds = self.adds + 1
        self.value = super.add(amount).value
        return self.value
    }
}"""
    )
    pdf.body("The install package includes examples\\calculator.agn. Run it with agen run examples\\calculator.agn.")

    pdf.h1("9. Libraries")
    pdf.body("Put reusable functions and classes in their own .agn file and import them with @. This is not Python's from ... import ....")
    pdf.code(
        """@import mathx                         # the whole library, used as mathx.add
@at mathx @import add, Counter        # selected functions and classes
@at "lib/mathx" @import add           # a path, in quotes
@imported                             # list the imports in this file"""
    )
    pdf.body("A name such as mathx looks for mathx.agn beside the program, then lib/mathx.agn beside the program, then each folder in the AGEN_LIB environment variable. lib.mathx is the same file as lib/mathx. A name cannot contain .., and an absolute path is rejected. Importing a file that imports you back is an error.")
    pdf.body("The install package has this pair ready. examples\\lib\\mathx.agn defines add and Counter. examples\\use_library.agn imports them. Run:")
    pdf.code("agen run examples\\use_library.agn")
    pdf.body("You should see 5, then 1, then 11, then a list of the imports in that file. Typing a line that is exactly @imported in the editor opens the same list. Hover @imported to see it without running.")

    pdf.h1("10. Workflows and steps")
    pdf.body("A workflow is a durable function. Agen derives a run id from the workflow name, its arguments, and the program text. Call it again with the same arguments and Agen continues that run. If the run already finished, Agen returns the saved result. Change the program, or the arguments, and you get a new run. agen run file.agn --fresh throws away the saved run and starts over.")
    pdf.body("A step is the unit of memory inside a workflow. When a step succeeds, the variables it wrote are saved. On the next run those steps are skipped and the variables are restored. Put network calls, ask, jev, now(), and anything else that would change if you ran it twice inside a step. Code outside a step is replayed, so it must be safe to run again.")
    pdf.code(
        """workflow daily_report(day) {
    step fetch with {retries: 4, backoff: 0.1} {
        prices = [101.5, 99.2, 103.8]
    }
    step analyze with {retries: 2, timeout: 30} {
        summary = ask(analyst, "Prices for " + day + ": " + str(prices))
    } fallback {
        summary = "analysis unavailable"
    }
    return summary
}"""
    )
    pdf.body("Options on a step:")
    pdf.table(
        ["Option", "Default", "Meaning"],
        [
            ["retries", "2", "Extra attempts after the first failure"],
            ["backoff", "0.2", "Starting delay in seconds; it doubles each try"],
            ["max_backoff", "10", "Longest delay"],
            ["timeout", "none", "Seconds allowed for one attempt"],
            ["jitter", "true", "Randomize the delay so many callers do not retry together"],
        ],
        [36, 28, 116],
    )
    pdf.body("If an attempt fails, its variable changes are discarded before the next try. If every retry fails and the step has a fallback block, that block runs and the workflow continues. If there is no fallback, the step fails, an alert is raised, and the error stops the workflow.")
    pdf.h2("What is retried")
    pdf.body("Agen retries failures that may succeed later: network errors, HTTP 429 and 5xx, timeouts, fail(), and chaos(). It does not retry mistakes in the program: syntax errors, unknown names, type errors, bad indexes, and HTTP 4xx. Fix those. Retrying will not.")
    pdf.body("A finished run leaves a .agen folder next to the program. It holds checkpoints and an event log. Deleting it is safe. The next run starts clean, which is the same idea as --fresh.")
    pdf.body("Steps inside a loop are numbered, such as classify and classify#1, so a resume can tell them apart. A step inside parallel work gets a key such as pmap0.2.load.")

    pdf.h1("11. Deploy and services")
    pdf.body("resource declares something the program needs, such as a resource group or a storage account. deploy creates or updates those resources for one environment, then runs its body. The local provider is the default, so the first deploy works on your machine with no cloud login. State is stored under .agen.")
    pdf.code(
        """resource group rg {
    location = "eastus"
}

resource storage reports {
    resource_group_name = rg
    account_tier        = "Standard"
}

deploy dev {
    service reporter with {restarts: 2} {
        result = daily_report("2026-11-16")
        print("report:", result)
        print("stored:", get(reports, "2026-11-16").summary)
    }
}"""
    )
    pdf.body("A service is a supervised block. If it crashes, Agen restarts it with backoff. Workflows it already started continue from the last finished step, so a restart does not repeat finished work. If it stays down, Agen raises an alert. restarts is how many restarts to allow. The default backoff is 0.5 seconds.")
    pdf.body("put(resource, key, value) stores a value. get(resource, key) reads it. Both must run inside deploy, because that is when the environment is active. A reference such as resource_group_name = rg tells Agen which resource has to exist first.")
    pdf.h2("Commands you will use here")
    pdf.code(
        """agen plan   report.agn                 # show what deploy would change
agen run    report.agn                 # deploy, then run the body
agen status report.agn                 # checkpoints and environments
agen export report.agn -o main.tf.json # Terraform JSON for Azure"""
    )

    pdf.h1("12. Agents")
    pdf.body("An agent is a configured model. ask(agent, prompt) sends one prompt and returns the text. The provider decides where that call goes.")
    pdf.code(
        """agent analyst {
    provider     = env("AGEN_LLM_PROVIDER", "mock")
    model        = "gpt-4o-mini"
    instructions = "You are a concise financial analyst."
    cache        = true
}

summary = ask(analyst, "Summarize these prices: " + str(prices))"""
    )
    pdf.bullet("mock is the default. It is deterministic and offline. Use it while you learn. No key is required.")
    pdf.bullet("openai uses OPENAI_API_KEY. OPENAI_BASE_URL is optional. model selects the model.")
    pdf.bullet("azure uses AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY. AZURE_OPENAI_API_VERSION is optional. Use deployment or model for the deployment name.")
    pdf.body("cache = true reuses the answer when the same agent is asked the same prompt again during one run. ask_all(agent, prompts) asks many prompts at the same time and keeps the results in the same order as the prompts. Put ask inside a step when it sits in a workflow, so a resume does not call the model again.")

    pdf.h1("13. Typed questions with JEV")
    pdf.body("jev asks a model for structured answers instead of a paragraph. Each question has a type, and the answer includes a confidence. This needs TYPESAFE_API_KEY. Skip this section until you have that key. The rest of Agen works without it.")
    pdf.code(
        """let questions = {
    department: choice("Which team should handle this", {
        billing: "Payment or subscription issues",
        technical: "Bugs or integration problems"
    }),
    urgent: noul("The message conveys urgency")
}

let a = jev("My integration keeps failing. Please help ASAP.", questions)
if a.urgent.noul >= 0.7 {
    print("page on-call: " + a.department.choice)
}"""
    )
    pdf.table(
        ["Question", "What you pass", "What you read back"],
        [
            ["choice", "A label and a description for each option", "choice, confidence, probabilities"],
            ["score", "A list of levels, lowest first", "score (0 is the first level), confidence, legend"],
            ["noul", "A statement", "noul, the probability it is true"],
        ],
        [28, 78, 74],
    )
    pdf.body("jev(input, questions) takes text or a map. Optional settings are model (default jev-latest), timeout, cache, and workers. jev_all(inputs, questions) asks the same questions of many inputs at the same time. Put these calls inside a step in a workflow.")
    pdf.body("Put TYPESAFE_API_KEY in a .env file in the same folder as the program, or set it in the environment. agen run loads that .env file. The key is sent only as an authorization header. It is not printed in the summary and it is not stored in the checkpoint.")

    pdf.h1("14. Doing work at the same time")
    pdf.body("Independent calls should not wait on each other. parallel runs each statement in the block at the same time, then continues. Each statement has its own scope. Names it assigns are visible after the block. Two statements that assign the same name are an error.")
    pdf.code(
        """parallel {
    prices = http_get("https://example.com/prices")
    news   = http_get("https://example.com/news")
    sentiment = ask(analyst, "Market mood today?")
}
print(len(prices), len(news), sentiment)"""
    )
    pdf.body("pmap(fn, items) calls one function on every item. Results stay in the same order as the items. The default is 8 workers. Pass a third argument to change that: pmap(count_primes, limits, 4). ask_all and jev_all follow the same idea.")
    pdf.body("Waiting overlaps: many HTTP or model calls can be in flight together. Pure number functions also use several CPU cores, because the fast engine allows that. Other Agen code runs one thread at a time. If any task fails, the rest still finish, and the first error in input order is the one that is raised.")
    pdf.body("A common shape is a class method that calls pmap on another method. The method is not a free name inside the class. Use self:")
    pdf.code(
        """class PrimeCounter {
    fn count_primes(limit) {
        count = 0
        for i in range(limit) {
            if i % 2 != 0 { count = count + 1 }
        }
        return count
    }
    fn count_primes_parallel(limits) {
        return pmap(self.count_primes, limits)
    }
}

let counter = PrimeCounter()
print(counter.count_primes_parallel([100000, 100000, 100000, 100000]))"""
    )
    pdf.body("pmap(count_primes, limits) from inside the class fails with 'count_primes' is not defined. The function is self.count_primes.")

    pdf.h1("15. Secrets")
    pdf.body("Do not write keys in a program. agen run always loads a .env file sitting next to the program. A line looks like TYPESAFE_API_KEY=your-key. That file stays on your machine. Do not send it to another person with the install package.")
    pdf.body("Key Vault is optional. Nothing contacts Azure unless the program calls get, names, creds, or load on KeyVaultCreds. A program that only uses .env never needs the Azure CLI.")
    pdf.code(
        """let creds = KeyVaultCreds("my-agen-vault")
let key = creds.get("AZURE-OPENAI-KEY")
print(key)                 # prints secret("AZURE-OPENAI-KEY"), not the value
creds.load()               # copies names into the environment for ask and jev"""
    )
    pdf.body("After load(), a vault name such as AZURE-OPENAI-KEY is also available as AZURE_OPENAI_KEY, because a Key Vault name cannot contain an underscore. Do not print env(\"AZURE_OPENAI_KEY\") afterward. That would show the raw value.")
    pdf.body("To point a project at a vault without putting the name in the program, sign in once with az login and save a vault.json file next to the program: { \"vault\": \"my-agen-vault\" }. The file stores the vault name only. The program can then call KeyVaultCreds(env(\"AGEN_KEYVAULT_NAME\")).")

    pdf.h1("16. What a run leaves behind")
    pdf.body("Next to the program, Agen creates .agen when a workflow or a deploy has something to remember.")
    pdf.bullet(".agen/runs/ holds one journal per workflow run. Completed steps are recorded there.")
    pdf.bullet(".agen/infra/ holds resource state for each environment, such as dev.")
    pdf.bullet(".agen/cloud/ holds data for the local provider, including values stored with put.")
    pdf.bullet(".agen/events.jsonl is a JSON log of steps, retries, replays, restarts, and deploys.")
    pdf.body("Alerts print to the error stream. If AGEN_ALERT_WEBHOOK is set, Agen also POSTs the alert as JSON with a text field, which Slack and Teams accept. Every run ends with one summary line: steps run, replayed, retried, recovered by fallback, failed, service restarts, alerts, parallel batches, and cache hits.")

    pdf.h1("17. Command reference")
    pdf.table(
        ["Command", "What it does"],
        [
            ["agen --version", "Show the version and whether the fast engine is on"],
            ["agen run file.agn", "Run the program"],
            ["agen check file.agn", "Parse the program and report ok"],
            ["agen plan file.agn", "Preview resource changes, without applying them"],
            ["agen status file.agn", "Show workflow journals and environments"],
            ["agen export file.agn -o main.tf.json", "Write Terraform JSON"],
            ["agen run file.agn --fresh", "Ignore the saved workflow result and start over"],
            ["agen run file.agn --no-native", "Run every function on the normal interpreter"],
            ["agen run file.agn -q", "Print program output and alerts only"],
        ],
        [78, 102],
    )
    pdf.body("agen run file.agn --crash-after STEP is a teaching switch. It stops the process after that step is saved, so you can run the file again and watch it resume. A real crash works the same way: run the file again.")

    pdf.h1("18. A first-day checklist")
    pdf.bullet("Install 64-bit Python 3.11, unzip Agen-0.1.0-win64.zip, and run install.ps1.")
    pdf.bullet("Open a new terminal. agen --version shows 0.1.0 and the native backend.")
    pdf.bullet("Reload Cursor or VS Code so .agn files have an icon and colors.")
    pdf.bullet("Run examples\\hello.agn, examples\\calculator.agn, and examples\\use_library.agn.")
    pdf.bullet("Write hello.agn yourself and run agen check, then agen run.")
    pdf.bullet("Add a workflow and a step only when a call must be retried or remembered.")
    pdf.bullet("Keep keys in .env. Do not paste them into the program, and do not print them.")
    pdf.bullet("Use provider \"mock\" until you are ready to call a real model.")
    pdf.ln(2)
    pdf.body("When those eight items work, you know enough to write a program that calls a function, keeps a class, imports a library, and, when you need it, survives a crash. Add deploy, an agent, or jev when the program actually needs them. Each of those stays quiet until you use it.")

    pdf.output(str(OUT))
    print(OUT)


if __name__ == "__main__":
    build()
