"""Which package a spec builds (E2E-SWE thin11 run, 2026-11-13).

protobuf-to-pydantic scored 35.7, the lowest of any build of it. Its spec is titled `# protobuf-to-pydantic` and
opens "Build `protobuf_to_pydantic`", but writes `google.protobuf.Any` and seven more proto names in fourteen code
spans against seven for its own package, so the surface test was written for a package called `google`. From session
6 on, eight names of the installed protobuf were reported as not importing, with the hint to add
`google/__init__.py` (which would have hidden protobuf); `close` was refused on them at session 9. The package the
spec does build was never checked. The same test was written in all 14 Horizon builds of this task since the big run.

Read against the directories the builds made, the guess was wrong for 12 of the benchmark's 96 specs. Eleven took a
variable the examples use (`request.body`, `coll.find`, `a.b`) or a dependency; none of the twelve wrong names was
ever imported, stated or in the title, and the title named the right package in ten."""
import subprocess
import sys

import surface

PROTO_LIKE = """# proto-to-models

Build `proto_to_models`, a library that turns Protobuf messages into model classes.
`proto_to_models/__init__.py` must export `msg_to_model`.

## Well-known types

Map `google.protobuf.Timestamp` to `datetime.datetime`, `google.protobuf.Duration` to `datetime.timedelta`,
`google.protobuf.Any` to the `Any` message, `google.protobuf.Empty` to `None` and `google.protobuf.Struct` to a dict.
Options extend `google.protobuf.FieldOptions`, `google.protobuf.MessageOptions` and `google.protobuf.OneofOptions`.

## Modules

`proto_to_models.gen_model` holds `clear_cache()`. `proto_to_models.template.Template` can be subclassed.
"""

SERVER_LIKE = """# quaver — an asynchronous HTTP server

Build `quaver`, a server in pure Python. `quaver.Application` is the entry point.

## Handlers

A handler takes the request: `request.body()`, `request.form()`, `request.headers`, `request.path`,
`request.query`, `request.cookies`. `response.set_status(404)` and `response.write(data)` send the reply.
"""


def test_the_title_names_the_package_although_a_dependency_is_written_more():
    assert surface.guess_package(PROTO_LIKE) == "proto_to_models"
    assert not any(name.startswith("google") for name in surface.refs(PROTO_LIKE, surface.guess_package(PROTO_LIKE)))


def test_the_title_names_the_package_although_a_variable_is_written_more():
    assert surface.guess_package(SERVER_LIKE) == "quaver"


def test_a_title_in_another_case_still_names_the_package():
    text = ("# DiskStore\n\n`diskstore.Cache` is the store.\n\n`cache.get(key)`, `cache.set(key, value)`, "
            "`cache.delete(key)`, `cache.touch(key)` and `cache.close()` are its methods.\n")
    assert surface.guess_package(text) == "diskstore"


def test_a_titled_package_with_no_names_to_check_leaves_no_variable_as_the_package():
    # pyclientgen: a command-line tool, so the spec gives no dotted names for it; the surface test was written for
    # `info`, a variable of its examples (thin4 run).
    text = ("# apigen\n\nBuild `apigen`, a command-line tool that reads an API description.\n\n"
            "Run `apigen generate` to write the client. The title comes from `info.title`, the version from "
            "`info.version`, each operation from `body.content` and `body.required`.\n")
    assert surface.guess_package(text) is None


def test_a_title_that_is_the_distribution_name_gives_way_to_the_imported_package():
    # PyYAML is imported as `yaml`: the title names nothing with dotted names, the imports do.
    text = ("# pyyamlish\n\nInstall `pyyamlish` with pip.\n\n```python\nimport yamlish\nyamlish.safe_load(text)\n```\n\n"
            "`yamlish.safe_load` reads a document, `yamlish.dump` writes one. `node.tag` and `node.value`, "
            "`node.start_mark`, `node.end_mark` describe a node.\n")
    assert surface.guess_package(text) == "yamlish"


def test_a_spec_that_says_what_the_package_is_importable_as():
    # SEC filing parser: the title is prose and the package is given in one sentence.
    text = ("# Filing parser\n\nBuild a library that reads a filing. The package must be importable as "
            "`filing_parser`.\n\n`from filing_parser import Parser, TitleElement`: the parser and one element.\n\n"
            "Setting `node.parent = other` moves a node; `tree.render()` prints the tree; `node.children` lists it.\n")
    assert surface.guess_package(text) == "filing_parser"


def test_a_title_that_names_nothing_written_as_code_changes_nothing():
    text = ("# A scheduler for jobs\n\n`sched4.Scheduler` runs jobs. `sched4.Job` is one job, "
            "`sched4.triggers.CronTrigger` a trigger.\n")
    assert surface.guess_package(text) == "sched4"


# --- the written test, when it checks a package that is not the project's ---------------------------------------

def _run_gate(root, gate, path):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(gate)],
                          cwd=root, capture_output=True, text=True, timeout=120, env={"PYTHONPATH": str(path)})


def test_a_namespace_package_of_another_library_is_not_to_be_given_an_init_file(tmp_path):
    # thin11 run, protobuf-to-pydantic: "google is a namespace package: add google/__init__.py" for five sessions.
    # The file would have hidden the installed protobuf; three earlier builds of this task did write it.
    installed = tmp_path / "site"
    (installed / "vendor" / "wire").mkdir(parents=True)
    (installed / "vendor" / "wire" / "__init__.py").write_text("")
    project = tmp_path / "project"
    (project / "mine").mkdir(parents=True)
    (project / "mine" / "__init__.py").write_text("")
    spec = project / "SPEC.md"
    spec.write_text("Map `vendor.wire.Any` to a message.\n")
    gate = project / "gates" / "test_surface.py"
    gate.parent.mkdir()
    surface.write_gate(spec, "vendor", gate)
    failed = _run_gate(project, gate, installed)
    assert failed.returncode == 1, failed.stdout
    said = " ".join(line for line in failed.stdout.splitlines() if "Failed: " in line)
    assert "not this project's" in said and "delete vendor.wire.Any from NAMES" in said, failed.stdout
    assert "add vendor/__init__.py" not in said, failed.stdout


def test_a_namespace_package_of_the_project_is_still_told_to_add_the_init_file(tmp_path):
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "dev.py").write_text("class ConsoleRenderer:\n    pass\n")
    spec = project / "SPEC.md"
    spec.write_text("Use pkg.dev.ConsoleRenderer.\n")
    gate = project / "gates" / "test_surface.py"
    gate.parent.mkdir()
    surface.write_gate(spec, "pkg", gate)
    failed = _run_gate(project, gate, tmp_path / "nothing")
    assert failed.returncode == 1 and "add pkg/__init__.py" in failed.stdout, failed.stdout
