import ast
import re
from inspect import cleandoc
from pathlib import Path


MODULES = [
    "db_ingest",
    "db_queries",
    "pdf_utils",
    "ocr_utils",
    "llm_extraction",
]

HIDDEN_MAIN_FUNCTIONS = {
    ("db_ingest", "reset_database"),
    ("db_ingest", "print_db_snapshot"),
}

SECTION_ORDER = [
    ("Database Setup And Ingest", ["db_ingest"]),
    ("Database Queries And Exports", ["db_queries"]),
    ("OCR And Extraction Pipeline", ["pdf_utils", "ocr_utils", "llm_extraction"]),
]

SECTION_BLURBS = {
    "Database Setup And Ingest": (
        "When to use this: creating/loading databases, inserting new receipts, "
        "and updating or deleting stored records."
    ),
    "Database Queries And Exports": (
        "When to use this: powering dashboards, summaries, reporting, and CSV/DataFrame exports."
    ),
    "OCR And Extraction Pipeline": (
        "When to use this: converting source files into OCR text and structured receipt JSON."
    ),
    "Advanced And Maintenance Functions": (
        "When to use this: debugging, destructive resets, or low-level utilities."
    ),
}


def parse_module_functions(module_name: str):
    src_path = Path("src/receipt_processor") / f"{module_name}.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    items = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue

        signature = f"{node.name}({ast.unparse(node.args)})"
        doc = cleandoc(ast.get_docstring(node) or "No docstring provided yet.")
        first_line = doc.splitlines()[0] if doc else "No summary provided."
        items.append(
            {
                "module": module_name,
                "name": node.name,
                "signature": signature,
                "doc": doc,
                "summary": first_line,
                "is_private": node.name.startswith("_"),
            }
        )
    return items


def function_sort_key(item):
    return (item["is_private"], item["name"].lower())


def format_docstring_for_markdown(doc: str):
    lines = doc.splitlines()
    out = []

    for line in lines:
        stripped = line.strip()

        # Convert section labels like "Args:" to markdown-friendly headers.
        if re.match(r"^[A-Za-z][A-Za-z0-9 _`'()/.-]*:\s*$", stripped):
            title = stripped[:-1].strip()
            out.append(f"**{title}**")
            out.append("")
            continue

        out.append(line)

    # Ensure markdown bullet lists are separated from surrounding text.
    normalized = []
    for i, line in enumerate(out):
        stripped = line.strip()
        prev = normalized[-1].strip() if normalized else ""
        next_line = out[i + 1].strip() if i + 1 < len(out) else ""

        if stripped.startswith("- ") and prev and not prev.startswith("- "):
            normalized.append("")
        normalized.append(line)
        if stripped.startswith("- ") and next_line and not next_line.startswith("- "):
            normalized.append("")

    return "\n".join(normalized).strip()


def render_function(item):
    fq = f'{item["module"]}.{item["name"]}'
    formatted_doc = format_docstring_for_markdown(item["doc"])
    lines = [
        f"### `{fq}`",
        "",
        f"*{item['summary']}*",
        "",
        "<details><summary>Details</summary>",
        "",
        "```python",
        item["signature"],
        "```",
        "",
        formatted_doc,
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def main():
    all_items = []
    for module in MODULES:
        all_items.extend(parse_module_functions(module))

    index = {(i["module"], i["name"]): i for i in all_items}

    out_lines = []

    hidden_items = []
    for section_title, module_names in SECTION_ORDER:
        out_lines.append(f"## {section_title}")
        out_lines.append("")
        blurb = SECTION_BLURBS.get(section_title)
        if blurb:
            out_lines.append(f"*{blurb}*")
            out_lines.append("")

        section_items = [i for i in all_items if i["module"] in module_names]
        section_items = [i for i in section_items if not i["is_private"]]
        visible_items = []
        for item in section_items:
            if (item["module"], item["name"]) in HIDDEN_MAIN_FUNCTIONS:
                hidden_items.append(item)
            else:
                visible_items.append(item)
        visible_items.sort(key=function_sort_key)

        for item in visible_items:
            out_lines.append(render_function(item))

    if hidden_items:
        out_lines.append("## Advanced And Maintenance Functions")
        out_lines.append("")
        blurb = SECTION_BLURBS.get("Advanced And Maintenance Functions")
        if blurb:
            out_lines.append(f"*{blurb}*")
            out_lines.append("")
        hidden_items.sort(key=function_sort_key)
        for item in hidden_items:
            out_lines.append(render_function(item))

    reference_dir = Path("reference")
    reference_dir.mkdir(parents=True, exist_ok=True)

    generated_path = reference_dir / "_api_generated.qmd"
    generated_path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")

    # Remove stale quartodoc artifacts to keep the docs directory clean.
    for qmd_file in reference_dir.glob("*.qmd"):
        if qmd_file.name != "_api_generated.qmd":
            qmd_file.unlink()


if __name__ == "__main__":
    main()
