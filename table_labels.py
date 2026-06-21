"""Table display labels for 27 Cafe & Bar dining areas."""

# Section A (7 tables) + Section B (6 tables) = 13 tables
TABLE_LABELS = [
    "A1", "A2", "A3", "A4", "A5", "A6", "A7",
    "B1", "B2", "B3", "B4", "B5", "B6",
]

RESTAURANT_NAME = "27 Cafe & Bar"


def label_for_number(n: int) -> str:
    if 1 <= n <= len(TABLE_LABELS):
        return TABLE_LABELS[n - 1]
    return f"T{n}"


def format_table_display(label: str) -> str:
    return f"Table {label}"


def get_table_label(table) -> str:
    return table.label or label_for_number(table.number)
