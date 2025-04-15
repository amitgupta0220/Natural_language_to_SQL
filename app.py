from flask import Flask, render_template, request, jsonify, send_file
import mysql.connector
import pandas as pd
from mysql.connector import Error
from dotenv import load_dotenv
import os
from openai import OpenAI
import io
import re

# Load environment variables
load_dotenv()
client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

app = Flask(__name__)

############################
#  OpenAI Helper Functions #
############################

def get_response(question, prompt):
    """
    Given a 'question' from the user and a system 'prompt',
    return the LLM's response or an error message.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": question}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content.strip(), None
    except Exception as e:
        return None, f"Error generating SQL query: {str(e)}"


def generate_prompt(schema_info):
    """
    Create a prompt that tells the LLM how to generate a SQL query from a given schema.
    """
    prompt = """You are an expert SQL query generator. Your task is to convert natural language questions into accurate SQL queries.
The database has the following schema:

"""
    for table, columns in schema_info:
        prompt += f"Table '{table}' with columns: {', '.join(columns)}\n"
    
    prompt += """
Guidelines for generating SQL queries:
1. Always use proper SQL syntax compatible with MySQL/MariaDB
2. Use appropriate JOIN conditions when querying multiple tables
3. Include WHERE clauses for filtering data
4. Use appropriate aggregation functions (COUNT, SUM, AVG, etc.) when needed
5. Return only the SQL query without any explanations or markdown formatting
6. Ensure the query is safe and follows best practices
7. Use proper table and column names exactly as provided in the schema

Example:
Question: "Show me all employees in the sales department"
SQL: SELECT * FROM employees WHERE department = 'sales'

Question: "What is the total revenue by region?"
SQL: SELECT region, SUM(revenue) as total_revenue FROM sales GROUP BY region

Remember to:
- Use exact table and column names from the schema
- Return only the SQL query without any additional text
- Ensure the query is syntactically correct
- Handle NULL values appropriately
- Use appropriate data types in comparisons
"""
    return prompt

#########################
#  Database Connection  #
#########################

def execute_query(user, password, host, database, query):
    """
    Execute a single SQL query and return (result, columns) or (error_str, [])
    """
    try:
        conn = mysql.connector.connect(
            user=user, password=password, host=host, database=database
        )
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchall()
            # If SELECT, we have columns. If not, cursor.description might be None
            column_names = [i[0] for i in cursor.description] if cursor.description else []
            conn.close()
            return result, column_names
    except Error as e:
        return f"Error: {e}", []
    return "Error: Could not connect to DB", []

def fetch_db_schema(user, password, host, database):
    """
    Return list of (table_name, [columns]) or a string if there's an error.
    """
    try:
        conn = mysql.connector.connect(
            user=user, password=password, host=host, database=database
        )
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (database,)
            )
            tables = cursor.fetchall()
            schema_info = []
            for tbl in tables:
                table_name = tbl[0]
                cursor.execute(f"SHOW COLUMNS FROM {table_name}")
                columns = cursor.fetchall()
                column_info = [col[0] for col in columns]
                schema_info.append((table_name, column_info))
            conn.close()
            return schema_info
    except Error as e:
        return f"Error: {e}"
    return "Error: Could not connect or fetch schema"

def fetch_databases(user, password, host):
    """
    Return a list of available databases or (None, error_string).
    """
    try:
        conn = mysql.connector.connect(user=user, password=password, host=host)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            databases = [
                db[0]
                for db in cursor.fetchall()
                if db[0] not in [
                    'information_schema', 'performance_schema', 'mysql', 'sys'
                ]
            ]
            conn.close()
            return databases, None
        return None, "Unable to connect"
    except Error as e:
        return None, f"Error connecting to database: {str(e)}"


#########################
#     Flask Routes      #
#########################

@app.route('/')
def index():
    # Render your trimmed-down HTML
    return render_template('index.html')

@app.route('/fetch-databases', methods=['POST'])
def get_databases():
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']

    if user and host:
        databases, error = fetch_databases(user, password, host)
        if error:
            return jsonify({"error": error})
        return jsonify({"databases": databases})
    else:
        return jsonify({"error": "Please provide user and host information."})

@app.route('/fetch-tables', methods=['POST'])
def fetch_tables():
    """
    Returns the list of tables for a given database so the front end
    can let the user select which table to drop.
    """
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']

    if not all([user, host, database]):
        return jsonify({"error": "Missing required info to fetch tables."})

    try:
        conn = mysql.connector.connect(user=user, password=password, host=host, database=database)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (database,)
            )
            table_names = [row[0] for row in cursor.fetchall()]
            conn.close()
            return jsonify({"tables": table_names})
        else:
            return jsonify({"error": "Could not connect to DB."})
    except Error as e:
        return jsonify({"error": f"Database error: {str(e)}"})
    except Exception as e:
        return jsonify({"error": f"Error fetching tables: {str(e)}"})


@app.route('/drop-table', methods=['POST'])
def drop_table():
    """
    Drops (deletes) the specified table from the selected database.
    """
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']
    table_name = request.form['table_name']

    if not all([user, host, database, table_name]):
        return jsonify({"error": "Missing required info to drop table."})
    
    try:
        conn = mysql.connector.connect(user=user, password=password, host=host, database=database)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
            conn.commit()
            conn.close()
            return jsonify({"message": f"Table '{table_name}' dropped successfully."})
        else:
            return jsonify({"error": "Could not connect to DB."})
    except Error as e:
        return jsonify({"error": f"Database error: {str(e)}"})
    except Exception as e:
        return jsonify({"error": f"Error dropping table: {str(e)}"})


@app.route('/create-database', methods=['POST'])
def create_database():
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']

    if not all([user, host, database]):
        return jsonify({"error": "Missing required information"})

    try:
        # Connect without specifying a DB to create it
        conn = mysql.connector.connect(user=user, password=password, host=host)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            conn.close()
            return jsonify({"message": f"Database '{database}' created successfully"})
        else:
            return jsonify({"error": "Cannot connect to MySQL server"})
    except Error as e:
        return jsonify({"error": f"Database error: {str(e)}"})
    except Exception as e:
        return jsonify({"error": f"Error creating database: {str(e)}"})

#############################
#  Executing a Query (LLM)  #
#############################

@app.route('/execute', methods=['POST'])
def execute():
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']
    question = request.form['question']

    if user and host and database and question:
        schema_info = fetch_db_schema(user, password, host, database)
        if isinstance(schema_info, str):  # Means it's an error string
            return jsonify({"error": schema_info})
        
        prompt = generate_prompt(schema_info)
        sql_query, error = get_response(question, prompt)
        
        if error:
            return jsonify({"error": error})
        if not sql_query:
            return jsonify({"error": "Failed to generate SQL query"})
        
        result, column_names = execute_query(user, password, host, database, sql_query)
        if isinstance(result, str):
            return jsonify({"error": result})
        
        # Convert to DataFrame + CSV
        df = pd.DataFrame(result, columns=column_names)
        csv_data = df.to_csv(index=False).encode('utf-8')
        
        return jsonify({
            "sql_query": sql_query,
            "result": df.to_dict(orient='records'),
            "columns": column_names,
            "csv": csv_data.decode('utf-8')
        })
    else:
        return jsonify({"error": "Please provide all the details."})

@app.route('/download', methods=['POST'])
def download():
    csv_data = request.form['csv_data']
    return send_file(
        io.BytesIO(csv_data.encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='query_results.csv'
    )

################################
#  Upload & Execute SQL File   #
#  (CREATE TABLE + INSERT)     #
################################

@app.route('/execute-sql-file', methods=['POST'])
def execute_sql_file():
    """
    1. Read the uploaded SQL file.
    2. Ask LLM to parse out both CREATE TABLE statements and INSERT statements.
    3. Execute all CREATE TABLE statements, then run all INSERT statements.
       *We replace 'INSERT INTO' with 'INSERT IGNORE INTO' to avoid duplicates.
    """
    if 'sql-file' not in request.files:
        return jsonify({"error": "No file uploaded"})
    
    file = request.files['sql-file']
    if file.filename == '':
        return jsonify({"error": "No file selected"})
    
    if not file.filename.endswith('.sql'):
        return jsonify({"error": "Invalid file type. Please upload a SQL file."})
    
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']
    
    if not all([user, host, database]):
        return jsonify({"error": "Missing required connection information"})
    
    try:
        # Read the SQL file content
        file_content = file.read()
        if not file_content:
            return jsonify({"error": "SQL file is empty"})
            
        sql_content = file_content.decode('utf-8')
        if not sql_content.strip():
            return jsonify({"error": "SQL file contains no statements"})

        # --- 1) LLM Prompt to extract BOTH CREATE TABLE + INSERT ---
        analysis_prompt = f"""
You are a SQL expert. I have a SQL file that needs to be analyzed.

SQL File Content:
{sql_content}

Your job:
- Find all CREATE TABLE statements (with table name, columns, and the raw create statement).
- Find all INSERT statements (e.g. INSERT INTO city VALUES (...)).

Return a JSON of this form:

{{
    "tables": [
      {{
        "table_name": "city",
        "columns": ["Name", "Population", "CountryCode"],
        "create_statement": "CREATE TABLE `city` (Name VARCHAR(255), Population INT, CountryCode CHAR(3));"
      }},
      ...
    ],
    "inserts": [
      "INSERT INTO `city` (Name, Population, CountryCode) VALUES ('New York', 9000000, 'USA');",
      ...
    ]
}}

Important:
- Only include valid CREATE TABLE and INSERT statements from the file.
- 'columns' can be just the column names (no data types).
- 'create_statement' must be a valid MySQL statement.
- 'inserts' must be valid MySQL INSERT statements.
- Return only the JSON, with no extra commentary or formatting.
- If there are no INSERT statements, return an empty list for inserts.
Example for an insert statement in the array: 
"INSERT INTO city (Name, Population, CountryCode) VALUES ('Mumbai', 20411000, 'IND');"
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a SQL expert that analyzes table structures and insert statements."},
                {"role": "user", "content": analysis_prompt}
            ],
            temperature=0.3
        )
        
        try:
            # Attempt to parse the LLM response as Python literal
            response_content = response.choices[0].message.content.strip()
            analysis = eval(response_content)  # or use json.loads(...) if it's valid JSON
            
            # Basic checks
            if not isinstance(analysis, dict):
                return jsonify({"error": "Invalid response format (not a dictionary)."})
            if "tables" not in analysis or "inserts" not in analysis:
                return jsonify({"error": "LLM response missing 'tables' or 'inserts' keys."})
            if not isinstance(analysis["tables"], list) or not isinstance(analysis["inserts"], list):
                return jsonify({"error": "LLM response 'tables'/'inserts' must be lists."})
            
            # Validate each table object
            for tbl in analysis["tables"]:
                if not all(k in tbl for k in ("table_name", "columns", "create_statement")):
                    return jsonify({"error": "Invalid table structure in LLM response."})

        except Exception as e:
            print(f"Error parsing response: {str(e)}")
            print(f"LLM response content: {response_content}")
            return jsonify({"error": f"Failed to parse LLM output: {str(e)}"})

        if not analysis["tables"] and not analysis["inserts"]:
            return jsonify({"error": "No CREATE TABLE or INSERT statements found in file."})

        # Debug prints (optional)
        print("=== Extracted Table Structures ===")
        for t in analysis["tables"]:
            print(f"Table: {t['table_name']}")
            print(f"Columns: {', '.join(t['columns'])}")
            print(f"Create Statement: {t['create_statement']}")

        print("\n=== Extracted Insert Statements ===")
        for ins in analysis["inserts"]:
            print(ins)

        # --- 2) Execute the statements in MySQL ---
        conn = mysql.connector.connect(user=user, password=password, host=host)
        cursor = conn.cursor()

        # Create the database if needed
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
        cursor.execute(f"USE `{database}`")

        execution_log = []
        tables_created = []
        inserts_executed = 0

        # --- 2A) CREATE TABLE statements ---
        for table_info in analysis["tables"]:
            try:
                cursor.execute(table_info["create_statement"])
                # If successful
                tables_created.append(table_info["table_name"])
                execution_log.append({
                    "type": "success",
                    "message": f"Created table '{table_info['table_name']}'"
                })
            except Error as e:
                msg = str(e).lower()
                if "already exists" in msg:
                    execution_log.append({
                        "type": "info",
                        "message": f"Table '{table_info['table_name']}' already exists"
                    })
                else:
                    execution_log.append({
                        "type": "error",
                        "message": f"Failed to create table '{table_info['table_name']}'",
                        "details": str(e)
                    })

        # --- 2B) INSERT statements ---
        # We'll do a simple textual replacement to transform "INSERT INTO"
        # into "INSERT IGNORE INTO" to avoid duplicates if there's a unique key
        pattern = re.compile(r'^\s*INSERT\s+INTO', re.IGNORECASE)

        for insert_stmt in analysis["inserts"]:
            # Check if it starts with 'INSERT INTO' (case-insensitive)
            if pattern.match(insert_stmt):
                insert_stmt = re.sub(pattern, 'INSERT IGNORE INTO', insert_stmt, count=1)
            
            try:
                cursor.execute(insert_stmt)
                # rowcount might be -1 for statements that don't return row counts,
                # but in MySQL it should reflect # of rows inserted
                if cursor.rowcount > 0:
                    inserts_executed += cursor.rowcount
                execution_log.append({
                    "type": "insert",
                    "message": f"Executed INSERT IGNORE: {insert_stmt[:60]}..."
                })
            except Error as e:
                execution_log.append({
                    "type": "error",
                    "message": f"INSERT failed: {insert_stmt[:60]}...",
                    "details": str(e)
                })

        conn.commit()
        conn.close()

        # Return final JSON
        return jsonify({
            "message": "Table creation and insertion completed",
            "details": {
                "tables_created": tables_created,
                "total_inserts_executed": inserts_executed,
                "execution_log": execution_log
            }
        })

    except Error as e:
        return jsonify({"error": f"Database error: {str(e)}"})
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return jsonify({"error": f"Error executing SQL file: {str(e)}"})


###################
#  Main Launcher  #
###################

if __name__ == '__main__':
    app.run(debug=True)
