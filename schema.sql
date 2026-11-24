CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    categoria TEXT NOT NULL,
    preco REAL NOT NULL CHECK (preco >= 0),
    quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0)
);

CREATE TABLE IF NOT EXISTS movimentacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida')),
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    data TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    produto_id INTEGER NOT NULL,
    FOREIGN KEY (produto_id) REFERENCES produtos (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_movimentacoes_produto_data
    ON movimentacoes (produto_id, data DESC);

CREATE INDEX IF NOT EXISTS idx_produtos_categoria
    ON produtos (categoria);

CREATE TRIGGER IF NOT EXISTS movimentacao_saida_saldo_suficiente
BEFORE INSERT ON movimentacoes
WHEN NEW.tipo = 'saida'
BEGIN
    SELECT CASE
        WHEN (SELECT quantidade FROM produtos WHERE id = NEW.produto_id) IS NULL
            THEN RAISE(ABORT, 'Produto não encontrado.')
        WHEN (SELECT quantidade FROM produtos WHERE id = NEW.produto_id) < NEW.quantidade
            THEN RAISE(ABORT, 'Estoque insuficiente para esta saída.')
    END;
END;

CREATE TRIGGER IF NOT EXISTS movimentacao_atualiza_estoque
AFTER INSERT ON movimentacoes
BEGIN
    UPDATE produtos
    SET quantidade = quantidade + CASE
        WHEN NEW.tipo = 'entrada' THEN NEW.quantidade
        ELSE -NEW.quantidade
    END
    WHERE id = NEW.produto_id;
END;
