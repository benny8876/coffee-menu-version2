"""Table display labels for 27 Cafe & Bar dining areas."""

# Section A (7 tables) + Section B (6 tables) = 13 tables
TABLE_LABELS = [
    "A1", "A2", "B1", "B2", "C1", "C2", "D1",
    "D2", "V-1", "V-2", "Order", "Main", "Extra",
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
