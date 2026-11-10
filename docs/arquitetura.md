# Arquitetura do MiniDB

Este documento descreve cada camada do banco, de cima para baixo, e as decisões tomadas em
cada uma.

## 1. Análise léxica — `sql/tokenizer.py`

Um laço único percorre a string caractere por caractere e produz `Token(type, value, pos)`.
A posição é guardada para que os erros de sintaxe digam *onde* o problema está:

```
SQLSyntaxError: esperado um identificador, encontrado '123' (posição 14)
```

Detalhes tratados:

- palavras-chave não diferenciam maiúsculas/minúsculas; identificadores são convertidos para
  minúsculas (a menos que estejam entre aspas duplas);
- strings usam aspas simples, e `''` dentro delas representa uma aspa (`'it''s'`);
- números com ponto e expoente (`1.5e-3`) viram `FLOAT`;
- comentários `-- ...` são ignorados.

## 2. Análise sintática — `sql/parser.py`

Parser **descendente recursivo**: cada regra da gramática é um método. Para expressões, cada
nível de precedência é uma função que chama a do nível seguinte:

```
expr        := or
or          := and ( OR and )*
and         := not ( AND not )*
not         := NOT not | comparison
comparison  := additive [ (= | <> | < | ...) additive
                        | IS [NOT] NULL
                        | [NOT] LIKE additive
                        | [NOT] BETWEEN additive AND additive
                        | [NOT] IN ( expr, ... ) ]
additive    := multiplicative ( (+ | - | ||) multiplicative )*
multiplicative := unary ( (* | / | %) unary )*
unary       := - unary | primary
primary     := literal | coluna | função( ... ) | ( expr )
```

Assim `1 + 2 * 3` vira `+(1, *(2, 3))` sem nenhuma tabela de precedência explícita. O
resultado é uma AST de `dataclasses` imutáveis (`sql/ast.py`); por serem imutáveis e
"hasheáveis", os nós de agregação (`COUNT(x)`) podem ser usados como chave de dicionário no
executor.

## 3. Catálogo — `catalog.py`

Guarda o esquema de cada tabela e faz a **coerção de tipos** na escrita: `INTEGER` aceita
`3.0` (vira `3`) mas recusa `3.5`; `REAL` aceita inteiros; `BOOLEAN` aceita `0`/`1`. Violações
de `NOT NULL` e de tipo geram `ConstraintError`.

Cada tabela é uma B+ tree. Se o esquema tem `PRIMARY KEY`, ela é a chave da árvore; senão um
`rowid` interno autoincrementado é usado (e persistido, para que IDs não sejam reaproveitados
depois de um restart).

## 4. B+ tree — `storage/btree.py`

| Operação | Custo |
|---|---|
| busca, inserção, remoção | O(log n) |
| varredura de intervalo com k resultados | O(log n + k) |

- `order` = número máximo de filhos de um nó interno; folhas guardam até `order - 1` chaves.
- Todas as chaves ficam nas **folhas**, que formam uma lista encadeada (`leaf.next`). É isso
  que torna `WHERE id BETWEEN a AND b` barato: desce até a folha de `a` e anda para a direita.

**Inserção.** Recursiva; quando um nó estoura, ele é dividido ao meio e a chave separadora
"sobe" para o pai. Na folha, a separadora é *copiada* (continua na folha da direita); no nó
interno ela é *movida*. Se a raiz se divide, a árvore cresce um nível.

**Remoção.** Também recursiva. Depois de remover de um filho, o pai verifica se ele ficou
abaixo da ocupação mínima e, nesta ordem, tenta:

1. pegar emprestado uma chave do irmão da esquerda;
2. pegar emprestado do irmão da direita;
3. fundir com o irmão da esquerda;
4. fundir com o irmão da direita.

Em nós internos o empréstimo é uma "rotação" passando pela chave separadora do pai. Se a
raiz interna fica com um único filho, a árvore perde um nível.

As separadoras do nó interno podem ficar "velhas" (apontar para uma chave que já foi
removida), mas continuam sendo limites válidos, então não é preciso atualizá-las.

`check_invariants()` valida a estrutura inteira e é chamado nos testes aleatórios.

## 5. Avaliação de expressões — `expressions.py`

Implementa a **lógica de três valores** do SQL: qualquer operação com `NULL` resulta em
`NULL`, exceto:

| a | b | a AND b | a OR b |
|---|---|---|---|
| TRUE | NULL | NULL | TRUE |
| FALSE | NULL | FALSE | NULL |
| NULL | NULL | NULL | NULL |

Um `WHERE` só aceita a linha quando o resultado é exatamente `TRUE`; por isso
`WHERE curso = NULL` nunca devolve nada e é preciso usar `IS NULL`.

A divisão entre inteiros trunca em direção a zero (`-7 / 2 = -3`), como em C e no SQLite —
diferente do `//` do Python, que arredonda para baixo.

## 6. Planejador — `planner.py`

O `WHERE` é quebrado nos termos ligados por `AND`. Termos do tipo `pk <op> literal` (em
qualquer ordem), `pk BETWEEN a AND b` e `pk IN (...)` são combinados em limites:

```
id > 10 AND id <= 20 AND ativo     →  RANGE (10, 20]  + filtro "ativo"
id IN (1, 2, 3) AND id >= 2        →  LOOKUP {2, 3}
id = 1 AND id = 2                  →  EMPTY
id = 1 OR id = 2                   →  SCAN (OR não é analisado)
```

O plano só precisa ser **conservador**: ele pode trazer linhas a mais, porque o `WHERE`
completo é reavaliado depois em cada linha, mas nunca pode deixar de trazer uma linha que
satisfaz a condição. O teste `test_index_results_match_full_scan` garante isso comparando o
resultado com e sem índice.

Literais de tipo incompatível com a chave (ex.: `id = 'abc'`) não usam o índice, já que a
comparação dentro da árvore falharia.

## 7. Executor — `database.py`

Um `SELECT` passa por um pipeline fixo:

```
plano de acesso → filtro WHERE → GROUP BY / agregação → projeção
               → DISTINCT → ORDER BY → OFFSET/LIMIT
```

`ORDER BY` com várias chaves é feito com várias ordenações **estáveis**, da última chave para a
primeira — o mesmo truque do radix sort. `NULL` é tratado como menor que qualquer valor.

Comandos que alteram dados seguem sempre duas fases:

1. **calcular** todas as mudanças e validar (tipos, `NOT NULL`, chave duplicada) sem tocar em
   nada;
2. **efetivar**: gravar um registro lógico no WAL e só então aplicá-lo na memória.

Se a fase 1 falhar, nada mudou — é isso que torna cada comando atômico. Um caso interessante é
`UPDATE t SET id = id + 1`: aplicado linha a linha, a linha 1 viraria 2 e colidiria com a linha
2 existente. Por isso o registro de update remove todas as chaves antigas antes de inserir as
novas, e a validação de duplicatas desconsidera chaves que também estão sendo alteradas.

## 8. Durabilidade — `storage/wal.py` e `storage/snapshot.py`

Na pasta do banco ficam dois arquivos:

```
meu_banco/
├── data.snapshot   estado completo no momento do último checkpoint (JSON)
└── wal.log         alterações feitas depois do checkpoint
```

Cada linha do WAL é `<crc32> <json>` e cada registro tem um **LSN** (log sequence number)
crescente. O snapshot guarda o LSN do último registro que ele já contém.

**Escrita:** `append` → `flush` → `fsync` → aplicar na memória.

**Checkpoint** (automático a cada N registros, ou com o comando `CHECKPOINT`):

1. grava o snapshot em `data.snapshot.tmp` e faz `fsync`;
2. `os.replace` para o nome definitivo (atômico);
3. esvazia o WAL.

**Recuperação ao abrir:**

1. carrega o snapshot (se existir);
2. relê o WAL, **ignorando registros com LSN ≤ LSN do snapshot**;
3. se a última linha do WAL estiver incompleta ou com CRC inválido, ela é descartada
   (o processo caiu no meio de um `write`); se uma linha do meio estiver inválida, o arquivo
   foi corrompido e a abertura falha com `StorageError`.

O passo 2 cobre a queda entre os passos 2 e 3 do checkpoint: o snapshot novo já contém as
mudanças, o WAL antigo ainda está lá, e sem o LSN elas seriam aplicadas duas vezes.
