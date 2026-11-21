
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "database"
DB_PATH = DATABASE_DIR / "business.db"


def get_connection():
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT,
                buying_price REAL NOT NULL DEFAULT 0,
                selling_price REAL NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 0,
                reorder_level INTEGER NOT NULL DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                sale_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                expense_date TEXT NOT NULL DEFAULT CURRENT_DATE
            );
        """)

        # Add account ownership to existing tables.
        tables = ["products", "sales", "suppliers", "expenses"]

        for table in tables:
            columns = [
                row["name"]
                for row in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            ]

            if "user_id" not in columns:
                conn.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD COLUMN user_id INTEGER REFERENCES users(id)
                    """
                )

        # Add expense categories without deleting old expenses.
        expense_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(expenses)"
            ).fetchall()
        ]

        if "category" not in expense_columns:
            conn.execute("""
                ALTER TABLE expenses
                ADD COLUMN category TEXT NOT NULL DEFAULT 'Other'
            """)

        # Save the buying price at the time each sale is recorded.
        sales_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(sales)"
            ).fetchall()
        ]

        if "cost_price" not in sales_columns:
            conn.execute("""
                ALTER TABLE sales
                ADD COLUMN cost_price REAL NOT NULL DEFAULT 0
            """)

            # Give existing sales the best available historical cost.
            conn.execute("""
                UPDATE sales
                SET cost_price = COALESCE(
                    (
                        SELECT buying_price
                        FROM products
                        WHERE products.id = sales.product_id
                    ),
                    0
                )
            """)


if __name__ == "__main__":
    initialize_database()
    print("Database initialized successfully.")
    print("Database location:", DB_PATH)
