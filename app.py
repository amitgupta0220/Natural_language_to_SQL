from flask import Flask, render_template, request, jsonify, send_file
import mysql.connector
import pandas as pd
from mysql.connector import Error
from dotenv import load_dotenv
import os
from openai import OpenAI
import io

# Load environment variables
load_dotenv()
client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

app = Flask(__name__)

# Function to get response from OpenAI API
def get_response(question, prompt):
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

# Function to execute the SQL query
def execute_query(user, password, host, database, query):
    try:
        conn = mysql.connector.connect(user=user, password=password, host=host, database=database)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchall()
            column_names = [i[0] for i in cursor.description]
            conn.close()
            return result, column_names
    except Error as e:
        return f"Error: {e}", []

# Function to fetch table and column information
def fetch_db_schema(user, password, host, database):
    try:
        conn = mysql.connector.connect(user=user, password=password, host=host, database=database)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (database,))
            tables = cursor.fetchall()
            schema_info = []
            for table in tables:
                table_name = table[0]
                cursor.execute(f"SHOW COLUMNS FROM {table_name}")
                columns = cursor.fetchall()
                column_info = [column[0] for column in columns]
                schema_info.append((table_name, column_info))
            conn.close()
            return schema_info
    except Error as e:
        return f"Error: {e}"

# Function to generate prompt based on schema
def generate_prompt(schema_info):
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
- Use appropriate data types in comparisons"""
    return prompt

# Function to fetch available databases
def fetch_databases(user, password, host):
    try:
        conn = mysql.connector.connect(user=user, password=password, host=host)
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            databases = [db[0] for db in cursor.fetchall() if db[0] not in ['information_schema', 'performance_schema', 'mysql', 'sys']]
            conn.close()
            return databases, None
    except Error as e:
        return None, f"Error connecting to database: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/execute', methods=['POST'])
def execute():
    user = request.form['user']
    password = request.form['password']
    host = request.form['host']
    database = request.form['database']
    question = request.form['question']

    if user and host and database and question:
        schema_info = fetch_db_schema(user, password, host, database)
        if isinstance(schema_info, str):
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
        
        df = pd.DataFrame(result, columns=column_names)
        csv = df.to_csv(index=False).encode('utf-8')
        return jsonify({
            "sql_query": sql_query,
            "result": df.to_dict(orient='records'),
            "columns": column_names,
            "csv": csv.decode('utf-8')
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

if __name__ == '__main__':
    app.run(debug=True)
