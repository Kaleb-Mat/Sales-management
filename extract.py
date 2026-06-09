import csv
import os
import pathlib
import psycopg2
from dotenv import load_dotenv
BASE_DIR = pathlib.Path(__file__).resolve().parent
# Prefer a .env file inside the local "db setup" folder, else fall back to default
dotenv_path = BASE_DIR / 'db setup' / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path)
else:
    load_dotenv()

# Support both POSTGRES_* and legacy names used earlier
host_name = os.environ.get('POSTGRES_HOST') or os.environ.get('host_name')
database_name = os.environ.get('POSTGRES_DB') or os.environ.get('database_name')
user_name = os.environ.get('POSTGRES_USER') or os.environ.get('user_name')
user_password = os.environ.get('POSTGRES_PASSWORD') or os.environ.get('password')
port = int(os.environ.get('POSTGRES_PORT') or os.environ.get('port') or 5432)

SCHEMA_FILE = BASE_DIR / 'db setup' / 'sales_schema.sql'
CSV_FILE = BASE_DIR / 'sales_data.csv'


def extract_data(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            data.append(row)
    return data


def clean_data(data):
    return [row for row in data if all(value.strip() for value in row.values())]


def filter_data(data, start_date='2020-12-01', end_date='2020-12-05'):
    return [row for row in data if start_date <= row['purchase_date'] <= end_date]


def total_spend(data):
    totals = {}
    for row in data:
        customer_id = row['customer_id']
        totals[customer_id] = totals.get(customer_id, 0.0) + float(row['purchase_amount'])
    return totals


def average_spend(data):
    counts = {}
    totals = {}
    for row in data:
        customer_id = row['customer_id']
        counts[customer_id] = counts.get(customer_id, 0) + 1
        totals[customer_id] = totals.get(customer_id, 0.0) + float(row['purchase_amount'])
    return {customer_id: totals[customer_id] / counts[customer_id] for customer_id in totals}


def quantity(data):
    counts = {}
    for row in data:
        key = (row['customer_id'], row['product_id'])
        counts[key] = counts.get(key, 0) + 1
    return [
        {'customer_id': customer_id, 'product_id': product_id, 'quantity': count}
        for (customer_id, product_id), count in counts.items()
    ]


def calculate_product_purchases(data, product_id):
    return sum(1 for row in data if row['product_id'] == product_id)


def connect_to_db():
    if not host_name or not database_name or not user_name or not user_password:
        print("Database connection parameters missing. Check your .env file or environment variables.")
        print(f"host={host_name!r}, db={database_name!r}, user={user_name!r}")
        return None
    try:
        return psycopg2.connect(
            host=host_name,
            port=port,
            database=database_name,
            user=user_name,
            password=user_password
        )
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None


def read_sql_file(path):
    with open(path, 'r', encoding='utf-8') as file:
        return file.read()


def create_tables_from_schema(connection, schema_path):
    sql_text = read_sql_file(schema_path)
    statements = [stmt.strip() for stmt in sql_text.split(';') if stmt.strip()]
    cursor = connection.cursor()
    try:
        for statement in statements:
            cursor.execute(statement)
        connection.commit()
        print('Schema applied: created/truncated sales_data, customer_spend, customer_products.')
    finally:
        cursor.close()


def verify_tables(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('sales_data', 'customer_spend', 'customer_products')
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        print('Tables present in DB:', tables)
        return tables
    finally:
        cursor.close()


def load_data_to_db(data, customer_spend_rows, customer_products_rows):
    connection = connect_to_db()
    if connection is None:
        print('Failed to connect to database, data not loaded.')
        return

    try:
        create_tables_from_schema(connection, SCHEMA_FILE)
        verify_tables(connection)
        cursor = connection.cursor()

        sales_insert = '''
            INSERT INTO sales_data (customer_id, product_id, purchase_date, purchase_amount)
            VALUES (%s, %s, %s, %s)
        '''
        for row in data:
            cursor.execute(
                sales_insert,
                (
                    int(row['customer_id']),
                    row['product_id'],
                    row['purchase_date'],
                    float(row['purchase_amount']) if row['purchase_amount'] else None,
                )
            )

        spend_insert = '''
            INSERT INTO customer_spend (customer_id, average_spend, total_spend)
            VALUES (%s, %s, %s)
        '''
        for row in customer_spend_rows:
            cursor.execute(spend_insert, (
                int(row['customer_id']),
                float(row['average_spend']),
                float(row['total_spend'])
            ))

        products_insert = '''
            INSERT INTO customer_products (customer_id, product_id, quantity)
            VALUES (%s, %s, %s)
        '''
        for row in customer_products_rows:
            cursor.execute(products_insert, (
                int(row['customer_id']),
                row['product_id'],
                int(row['quantity'])
            ))

        connection.commit()
    except Exception as e:
        print(f"Error loading data to database: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()


if __name__ == '__main__':
    raw_data = extract_data(CSV_FILE)
    cleaned_data = clean_data(raw_data)
    filtered_data = filter_data(cleaned_data)

    total_spend_map = total_spend(filtered_data)
    average_spend_map = average_spend(filtered_data)

    customer_spend_rows = [
        {
            'customer_id': customer_id,
            'average_spend': average_spend_map[(customer_id)],
        
            'total_spend': total_spend_map[(customer_id)]
        }
        for customer_id in total_spend_map
    ]
    customer_products_rows = quantity(filtered_data)

    print(f"Loaded {len(filtered_data)} rows after cleaning and filtering.")
    load_data_to_db(filtered_data, customer_spend_rows, customer_products_rows)
