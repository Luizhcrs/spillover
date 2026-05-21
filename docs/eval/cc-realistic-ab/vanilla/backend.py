from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import sqlite3
import json
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE = "barbearia.db"

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS barbeiros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        experiencia INTEGER NOT NULL,
        especialidade TEXT NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        barbeiros_data = [
            ('Joao', 15, 'degrade'),
            ('Pedro', 8, 'barba/bigode'),
            ('Carlos', 5, 'cortes jovens')
        ]
        cursor.executemany('INSERT INTO barbeiros (nome, experiencia, especialidade) VALUES (?, ?, ?)', barbeiros_data)
    
    cursor.execute('SELECT COUNT(*) FROM servicos')
    if cursor.fetchone()[0] == 0:
        servicos_data = [
            ('Corte Masculino', 45.0, 30),
            ('Barba Completa', 35.0, 30),
            ('Combo Cabelo+Barba', 75.0, 60),
            ('Pigmentacao', 90.0, 90),
            ('Sobrancelha', 20.0, 15)
        ]
        cursor.executemany('INSERT INTO servicos (nome, preco, duracao_min) VALUES (?, ?, ?)', servicos_data)
    
    conn.commit()
    conn.close()

init_db()

@app.get("/api/barbeiros")
def get_barbeiros():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT id, nome, experiencia, especialidade FROM barbeiros')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/servicos")
def get_servicos():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT id, nome, preco, duracao_min FROM servicos')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/agendamentos")
def create_agendamento(data: dict):
    barbeiro_id = data.get('barbeiro_id')
    servico_id = data.get('servico_id')
    cliente_nome = data.get('cliente_nome')
    cliente_telefone = data.get('cliente_telefone')
    data_hora = data.get('data_hora')
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('INSERT INTO agendamentos (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status) VALUES (?, ?, ?, ?, ?, ?)',
                   (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, 'confirmado'))
    
    agendamento_id = cursor.lastrowid
    conn.commit()
    
    cursor.execute('SELECT nome FROM barbeiros WHERE id = ?', (barbeiro_id,))
    barbeiro_nome = cursor.fetchone()[0]
    
    cursor.execute('SELECT nome, preco FROM servicos WHERE id = ?', (servico_id,))
    servico_row = cursor.fetchone()
    servico_nome = servico_row[0]
    servico_preco = servico_row[1]
    
    conn.close()
    
    print(f"[WhatsApp Mock] Enviando para {cliente_telefone}: Agendamento confirmado com {barbeiro_nome} para {servico_nome} (R${servico_preco}) em {data_hora}")
    
    return {
        'id': agendamento_id,
        'barbeiro_id': barbeiro_id,
        'servico_id': servico_id,
        'cliente_nome': cliente_nome,
        'cliente_telefone': cliente_telefone,
        'data_hora': data_hora,
        'status': 'confirmado'
    }

@app.delete("/api/agendamentos/{agendamento_id}")
def delete_agendamento(agendamento_id: int):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT data_hora FROM agendamentos WHERE id = ?', (agendamento_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Agendamento nao encontrado")
    
    data_hora_str = row[0]
    data_hora = datetime.fromisoformat(data_hora_str)
    agora = datetime.now()
    diff = (data_hora - agora).total_seconds() / 3600
    
    if diff < 1:
        conn.close()
        raise HTTPException(status_code=403, detail="Cancelamento nao permitido: menos de 1 hora antes")
    
    cursor.execute('DELETE FROM agendamentos WHERE id = ?', (agendamento_id,))
    conn.commit()
    conn.close()
    
    return {'mensagem': 'Agendamento cancelado'}

@app.get("/api/agendamentos")
def get_agendamentos(telefone: Optional[str] = None):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if telefone:
        cursor.execute('SELECT id, barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status FROM agendamentos WHERE cliente_telefone = ?', (telefone,))
    else:
        cursor.execute('SELECT id, barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status FROM agendamentos')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

@app.get("/")
def serve_frontend():
    return FileResponse("frontend.html", media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

```