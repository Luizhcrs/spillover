CREATE TABLE barbeiros (
    id INTEGER PRIMARY KEY,
    nome TEXT,
    experiencia INTEGER,
    especialidade TEXT
);

CREATE TABLE servicos (
    id INTEGER PRIMARY KEY,
    nome TEXT,
    preco REAL,
    duracao_min INTEGER
);

CREATE TABLE agendamentos (
    id INTEGER PRIMARY KEY,
    barbeiro_id INTEGER,
    servico_id INTEGER,
    cliente_nome TEXT,
    cliente_telefone TEXT,
    data_hora TEXT,
    status TEXT DEFAULT 'pendente',
    FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id),
    FOREIGN KEY (servico_id) REFERENCES servicos(id)
);
```