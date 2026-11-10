-- Exemplo: python -m minidb ./escola < examples/escola.sql

CREATE TABLE IF NOT EXISTS disciplinas (
    codigo TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    creditos INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS matriculas (
    id INTEGER PRIMARY KEY,
    aluno TEXT NOT NULL,
    disciplina TEXT NOT NULL,
    nota REAL
);

INSERT INTO disciplinas VALUES
    ('ED',  'Estruturas de Dados',        4),
    ('BD',  'Banco de Dados',             4),
    ('SO',  'Sistemas Operacionais',      4),
    ('PAA', 'Projeto e Análise de Algoritmos', 6);

INSERT INTO matriculas VALUES
    (1, 'ana',   'ED',  9.0),
    (2, 'ana',   'BD',  8.5),
    (3, 'bruno', 'ED',  6.0),
    (4, 'bruno', 'SO',  NULL),
    (5, 'carla', 'PAA', 7.5),
    (6, 'carla', 'BD',  9.5),
    (7, 'diego', 'ED',  4.5);

-- média por disciplina (quem ainda não tem nota fica de fora do AVG)
SELECT disciplina, COUNT(*) AS alunos, COUNT(nota) AS avaliados, ROUND(AVG(nota), 2) AS media
FROM matriculas
GROUP BY disciplina
ORDER BY media DESC;

-- aprovados em ED
SELECT aluno, nota FROM matriculas WHERE disciplina = 'ED' AND nota >= 6 ORDER BY nota DESC;

-- matrículas sem nota lançada
SELECT * FROM matriculas WHERE nota IS NULL;

-- o planejador usa a chave primária aqui
EXPLAIN SELECT * FROM matriculas WHERE id BETWEEN 2 AND 4;
