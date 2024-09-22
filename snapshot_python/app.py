from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from decimal import Decimal
import logging

app = Flask(__name__)
DATABASE_FILE = 'balances.db'

# 로그 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_db_connection():
    """
    데이터베이스 연결을 생성하고, WAL 모드를 활성화하며, 타임아웃을 설정합니다.
    """
    conn = sqlite3.connect(DATABASE_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL 모드 활성화
    conn.execute('PRAGMA journal_mode=WAL;')
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
    cursor = conn.cursor()
    
    try:
        # 총 계정 수 가져오기
        cursor.execute('SELECT COUNT(*) as count FROM balances')
        total_accounts = cursor.fetchone()['count']
        
        # 계정 목록 가져오기
        # 정렬 컬럼을 직접 쿼리에 삽입하면 SQL 인젝션 위험이 있으므로 안전하게 처리
        if sort == 'balance':
            sort_column = 'CAST(balance AS REAL)'
        else:
            sort_column = sort
        
        query = f'''
            SELECT address, balance, creation_block
            FROM balances
            ORDER BY {sort_column} {order}
            LIMIT ? OFFSET ?
        '''
        cursor.execute(query, (per_page, offset))
        accounts = cursor.fetchall()
    except sqlite3.OperationalError as e:
        logging.error(f"데이터베이스 접근 오류: {e}")
        accounts = []
        total_accounts = 0
    finally:
        conn.close()
    
    total_pages = (total_accounts + per_page - 1) // per_page
    
    return render_template('accounts.html', accounts=accounts, page=page, total_pages=total_pages, sort=sort, order=order)

@app.route('/')
def index():
    return redirect(url_for('accounts'))

@app.route('/account', methods=['GET'])
def account_detail():
    address = request.args.get('address')
    if not address:
        return '주소가 제공되지 않았습니다.', 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM balances WHERE address = ?', (address,))
    account = cursor.fetchone()
    
    if account is None:
        conn.close()
        return '계정을 찾을 수 없습니다.', 404
    
    # 해당 계정의 거래 내역 가져오기
    cursor.execute('''
        SELECT * FROM transactions
        WHERE from_address = ? OR to_address = ?
        ORDER BY block_number DESC
    ''', (address, address))
    transactions = cursor.fetchall()
    conn.close()
    
    return render_template('account_detail.html', account=account, transactions=transactions)

# 기타 필요한 라우트 및 함수들...

if __name__ == '__main__':
    app.run(debug=False)
