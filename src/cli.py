"""Typer CLI for annotation review (e.g. review queue against the API)."""
from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.config import settings

app = typer.Typer(help="Finance Copilot CLI", no_args_is_help=True)
console = Console()


@app.command()
def review():
    """Work through low-confidence model annotations in the review queue."""
    base = settings.api_base_url

    try:
        resp = httpx.get(f"{base}/annotations/review-queue", timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(f"[red]Could not reach API at {base}: {exc}[/red]")
        raise typer.Exit(1)

    items = resp.json()
    if not items:
        console.print("[green]Review queue is empty — nothing to review.[/green]")
        return

    total = len(items)
    for i, item in enumerate(items, start=1):
        console.print()
        content = Text()
        content.append(f"Date:         {item['txn_date']}\n")
        content.append(f"Amount:       ₹ {item['amount']:,.2f}  ({item['debit_credit']})\n")
        content.append(f"Description:  {item['raw_description']}\n\n")
        content.append(f"Model guessed:  {item['category']}", style="bold cyan")
        if item.get("subcategory"):
            content.append(f" / {item['subcategory']}", style="cyan")
        content.append(f"  (confidence: {item['confidence']:.2f})\n")
        if item.get("merchant"):
            content.append(f"Merchant:       {item['merchant']}\n")
        if item.get("reasoning"):
            content.append(f'Reasoning:      "{item["reasoning"]}"\n')

        console.print(
            Panel(
                content,
                title=f"Transaction {i} of {total}",
                border_style="yellow",
            )
        )
        console.print("[c] confirm    [e] edit    [s] skip    [q] quit")
        choice = typer.prompt("", default="s").strip().lower()

        if choice == "q":
            console.print("Bye.")
            break
        elif choice == "s":
            continue
        elif choice == "c":
            _patch(base, item["id"], {
                "category": item["category"],
                "subcategory": item.get("subcategory"),
                "merchant": item.get("merchant"),
                "confidence": item["confidence"],
            })
            console.print("[green]Confirmed.[/green]")
        elif choice == "e":
            category = typer.prompt("Category", default=item["category"])
            subcategory = typer.prompt("Subcategory (leave blank to clear)", default="") or None
            merchant = typer.prompt("Merchant (leave blank to keep)", default=item.get("merchant") or "") or None
            _patch(base, item["id"], {
                "category": category,
                "subcategory": subcategory,
                "merchant": merchant,
            })
            console.print("[green]Updated.[/green]")
        else:
            console.print("[dim]Unknown key — skipping.[/dim]")


def _patch(base: str, annotation_id: str, payload: dict) -> None:
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        resp = httpx.patch(f"{base}/annotations/{annotation_id}", json=payload, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(f"[red]PATCH failed: {exc}[/red]")


@app.command()
def version():
    """Show version."""
    console.print("finance-copilot 0.1.0")


if __name__ == "__main__":
    app()
