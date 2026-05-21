from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import sqlite3
from datetime import datetime, timedelta
from pydantic import BaseModel
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "barbearia.db"

class Barbeiro(BaseModel):
    id: int
    nome: str
    experiencia: int
    especialidade: str

class Servico(BaseModel):
    id: int
    nome: str
    preco: float
    duracao_min: int

class Agendamento(BaseModel):
    id: int
    barbeiro_id: int
    servico_id: int
    cliente_nome: str
    cliente_telefone: str
    data_hora: str
    status: str

class AgendamentoCreate(BaseModel):
    barbeiro_id: int
    servico_id: int
    cliente_nome: str
    cliente_telefone: str
    data_hora: str

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS barbeiros (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        experiencia INTEGER NOT NULL,
        especialidade TEXT NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        preco REAL NOT NULL,
        duracao_min INTEGER NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barbeiro_id INTEGER NOT NULL,
        servico_id INTEGER NOT NULL,
        cliente_nome TEXT NOT NULL,
        cliente_telefone TEXT NOT NULL,
        data_hora TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id),
        FOREIGN KEY (servico_id) REFERENCES servicos(id)
    )
    ''')
    
    cursor.execute('SELECT COUNT(*) FROM barbeiros')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (1, ?, ?, ?)', ('Joao', 15, 'degradê'))
        cursor.execute('INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (2, ?, ?, ?)', ('Pedro', 8, 'barba'))
        cursor.execute('INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (3, ?, ?, ?)', ('Carlos', 5, 'jovens'))
    
    cursor.execute('SELECT COUNT(*) FROM servicos')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (1, ?, ?, ?)', ('Corte Masculino', 45.0, 30))
        cursor.execute('INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (2, ?, ?, ?)', ('Barba Completa', 35.0, 30))
        cursor.execute('INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (3, ?, ?, ?)', ('Combo', 75.0, 60))
        cursor.execute('INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (4, ?, ?, ?)', ('Pigmentação', 90.0, 90))
        cursor.execute('INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (5, ?, ?, ?)', ('Sobrancelha', 20.0, 15))
    
    conn.commit()
    conn.close()

init_db()

@app.get("/")
async def root():
    return FileResponse("frontend.html")

@app.get("/api/barbeiros")
async def get_barbeiros():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, nome, experiencia, especialidade FROM barbeiros')
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "nome": r[1], "experiencia": r[2], "especialidade": r[3]} for r in rows]

@app.get("/api/servicos")
async def get_servicos():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, nome, preco, duracao_min FROM servicos')
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "nome": r[1], "preco": r[2], "duracao_min": r[3]} for r in rows]

@app.post("/api/agendamentos")
async def create_agendamento(agendamento: AgendamentoCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT INTO agendamentos (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (agendamento.barbeiro_id, agendamento.servico_id, agendamento.cliente_nome, agendamento.cliente_telefone, agendamento.data_hora, 'confirmado'))
    
    conn.commit()
    agendamento_id = cursor.lastrowid
    conn.close()
    
    print(f"WhatsApp Mock: Olá {agendamento.cliente_nome}, agendamento confirmado pra {agendamento.data_hora}")
    
    return {"id": agendamento_id, "status": "confirmado"}

@app.delete("/api/agendamentos/{agendamento_id}")
async def delete_agendamento(agendamento_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT data_hora FROM agendamentos WHERE id = ?', (agendamento_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    data_hora_str = result[0]
    data_hora = datetime.fromisoformat(data_hora_str)
    agora = datetime.now()
    diferenca = data_hora - agora
    
    if diferenca.total_seconds() < 3600:
        conn.close()
        raise HTTPException(status_code=403, detail="Cancelamento não permitido com menos de 1 hora de antecedência")
    
    cursor.execute('DELETE FROM agendamentos WHERE id = ?', (agendamento_id,))
    conn.commit()
    conn.close()
    
    return {"status": "cancelado"}

@app.get("/api/agendamentos")
async def get_agendamentos(telefone: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status FROM agendamentos WHERE cliente_telefone = ?', (telefone,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "barbeiro_id": r[1], "servico_id": r[2], "cliente_nome": r[3], "cliente_telefone": r[4], "data_hora": r[5], "status": r[6]} for r in rows]
```

```