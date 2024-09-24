#!/bin/bash

set -e  # 명령어 실행 실패 시 스크립트 중지

# Python 버전 설정
REQUIRED_PYTHON_VERSION="3.10.6"
VENV_DIR=".venv"

# pyenv로 Python 버전 설정
check_python_version() {
    INSTALLED_PYTHON_VERSION=$(pyenv versions --bare | grep "$REQUIRED_PYTHON_VERSION")
    if [[ -z "$INSTALLED_PYTHON_VERSION" ]]; then
        echo "Python $REQUIRED_PYTHON_VERSION is not installed via pyenv. Installing it now..."
        pyenv install $REQUIRED_PYTHON_VERSION
    fi
}

# pyenv를 사용하여 Python 버전 설정 함수
use_pyenv_python() {
    echo "Setting Python version to $REQUIRED_PYTHON_VERSION using pyenv..."
    pyenv shell $REQUIRED_PYTHON_VERSION
    if [[ $? -ne 0 ]]; then
        echo "Failed to set Python $REQUIRED_PYTHON_VERSION via pyenv."
        exit 1
    fi
    echo "Using Python version: $(python --version)"
}

# 가상 환경 생성 함수
create_venv() {
    echo "Creating virtual environment with Python $REQUIRED_PYTHON_VERSION..."
    python -m venv "$VENV_DIR"
    echo "Virtual environment created successfully."
}

# 가상 환경이 존재하는지 확인
if [ ! -d "$VENV_DIR" ]; then
    echo "No virtual environment found. Checking and setting up Python $REQUIRED_PYTHON_VERSION with pyenv."
    check_python_version  # pyenv에서 Python 3.10.6이 설치되어 있는지 확인
    use_pyenv_python  # Python 3.10.6을 pyenv로 설정
    create_venv  # 가상 환경 생성
else
    echo "Virtual environment found. Proceeding with activation."
fi

# 가상 환경 활성화
echo "Activating virtual environment"
source "$VENV_DIR/Scripts/activate"

# 패키지 설치
if [ -f "requirements.txt" ]; then
    echo "Installing required packages from requirements.txt..."
    pip install -r requirements.txt
else
    echo "requirements.txt not found, skipping package installation."
fi

# Flask 환경 변수 설정
export FLASK_APP=app.py
export FLASK_ENV=development

# Flask 애플리케이션 실행
echo "Running Flask app..."
flask run

# http://localhost:5000/accounts
