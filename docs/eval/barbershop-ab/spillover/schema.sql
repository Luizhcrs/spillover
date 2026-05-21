CREATE TABLE barbeiros (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    experiencia INTEGER NOT NULL,
    especialidade TEXT NOT NULL
);

CREATE TABLE servicos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    preco REAL NOT NULL,
    duracao_min INTEGER NOT NULL
);

CREATE TABLE agendamentos (
    id INTEGER PRIMARY KEY,
    barbeiro_id INTEGER NOT NULL,
    servico_id INTEGER NOT NULL,
    cliente_nome TEXT NOT NULL,
    cliente_telefone TEXT NOT NULL,
    data_hora TIMESTAMP NOT NULL,
    status TEXT DEFAULT 'pendente',
    FOREIGN KEY(barbeiro_id) REFERENCES barbeiros(id),
    FOREIGN KEY(servico_id) REFERENCES servicos(id)
);