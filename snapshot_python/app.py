from flask import Flask, render_template, request, redirect, url_for
import mysql.connector
from decimal import Decimal
import logging
import os
from dotenv import load_dotenv
from datetime import datetime

app = Flask(__name__)

# 로그 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# .env 파일 로드
load_dotenv()

# MySQL 접속 정보 불러오기
MYSQLDB_HOST = os.getenv('MYSQLDB_HOST')
MYSQLDB_USER = os.getenv('MYSQLDB_USER')
MYSQLDB_PASSWORD = os.getenv('MYSQLDB_PASSWORD')
MYSQLDB_DATABASE = os.getenv('MYSQLDB_DATABASE')

def get_db_connection():
    """
    MySQL 데이터베이스 연결을 생성합니다.
    """
    conn = mysql.connector.connect(
        host=MYSQLDB_HOST,
        user=MYSQLDB_USER,
        password=MYSQLDB_PASSWORD,
        database=MYSQLDB_DATABASE,
        autocommit=True  # 자동 커밋 설정
    )
    return conn

def format_esn_balance(balance_str):
    """
    Raw balance string (Wei 단위)를 ESN 단위로 변환하고 포맷팅합니다.
    예: "1000000000000000000" -> "1.000000000000000000 ESN"
    """
    try:
        # 소수점 18자리로 변환
        balance_decimal = Decimal(balance_str) / Decimal('1000000000000000000')
        # 18자리 소수점 유지 및 콤마 추가
        formatted_balance = "{:,.18f} ESN".format(balance_decimal)
        return formatted_balance
    except Exception as e:
        logging.error(f"잔액 포맷팅 오류: {e}")
        return balance_str  # 오류 발생 시 원본 문자열 반환

# 커스텀 필터 등록
app.jinja_env.filters['format_esn'] = format_esn_balance

def format_datetime(value):
    """
    유닉스 타임스탬프를 'YYYY-MM-DD HH:MM:SS' 형식의 문자열로 변환합니다.
    """
    try:
        dt = datetime.fromtimestamp(value)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logging.error(f"타임스탬프 포맷팅 오류: {e}")
        return value  # 오류 발생 시 원본 값을 반환
    
app.jinja_env.filters['format_datetime'] = format_datetime

@app.route('/accounts')
def accounts():
    # 페이지, 정렬 등의 파라미터 처리
    sort = request.args.get('sort', 'balance')
    order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = 50  # 한 페이지당 표시할 계정 수

    valid_sort_columns = ['address', 'balance', 'creation_block']
    if sort not in valid_sort_columns:
        sort = 'balance'

    if order not in ['asc', 'desc']:
        order = 'desc'

    offset = (page - 1) * per_page

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 총 계정 수 가져오기
        cursor.execute('SELECT COUNT(*) as count FROM balances')
        result = cursor.fetchone()
        total_accounts = result['count'] if result else 0

        # 계정 목록 가져오기
        sort_column = sort  # 이미 유효성 검사를 했으므로 안전

        query = f'''
            SELECT address, balance, creation_block
            FROM balances
            ORDER BY {sort_column} {order}
            LIMIT %s OFFSET %s
        '''
        cursor.execute(query, (per_page, offset))
        accounts = cursor.fetchall()
    except mysql.connector.Error as e:
        logging.error(f"데이터베이스 접근 오류: {e}")
        accounts = []
        total_accounts = 0
    finally:
        cursor.close()
        conn.close()

    total_pages = (total_accounts + per_page - 1) // per_page

    return render_template('accounts.html', accounts=accounts, page=page, total_pages=total_pages, sort=sort, order=order)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/account', methods=['GET'])
def account_detail():
    address = request.args.get('address')
    if not address:
        return '주소가 제공되지 않았습니다.', 400

    # 거래 내역 페이지네이션을 위한 파라미터 처리
    tx_page = int(request.args.get('tx_page', 1))
    tx_per_page = 50  # 한 페이지당 표시할 거래 수
    tx_offset = (tx_page - 1) * tx_per_page

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('SELECT * FROM balances WHERE address = %s', (address,))
    account = cursor.fetchone()

    if account is None:
        cursor.close()
        conn.close()
        return '계정을 찾을 수 없습니다.', 404

    # 해당 계정의 총 거래 수 가져오기
    cursor.execute('''
        SELECT COUNT(*) as count FROM transactions
        WHERE from_address = %s OR to_address = %s
    ''', (address, address))
    result = cursor.fetchone()
    total_transactions = result['count'] if result else 0

    total_pages = (total_transactions + tx_per_page - 1) // tx_per_page

    # 해당 계정의 거래 내역 가져오기 (페이지네이션 적용)
    cursor.execute('''
        SELECT * FROM transactions
        WHERE from_address = %s OR to_address = %s
        ORDER BY block_number DESC
        LIMIT %s OFFSET %s
    ''', (address, address, tx_per_page, tx_offset))
    transactions = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'account_detail.html',
        account=account,
        transactions=transactions,
        tx_page=tx_page,
        total_pages=total_pages
    )

# 기타 필요한 라우트 및 함수들...

if __name__ == '__main__':
    app.run(debug=False)
