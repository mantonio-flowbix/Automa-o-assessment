# flowbix-assess

MVP de automação do assessment de **Infraestrutura + Zabbix** da Flowbix.
Coleta dados do ambiente do cliente, roda um motor de regras baseado em boas
práticas, e gera um relatório HTML com achados e recomendações — no mesmo
espírito de seções do assessment manual (Arquitetura → Processamento → SO →
Banco de Dados → Templates → Mídias → Dashboards).

Grafana existe no código como módulo opcional, mas **não é prioridade** — só
roda se você configurar `grafana:` no YAML do cliente; se não configurar, ele
simplesmente não aparece no relatório.

Tudo é **read-only**: só métodos `*.get` na API do Zabbix, só `SELECT` no
MySQL, e o probe de infraestrutura só lê `/proc`, `/etc/os-release` e chama
`ss`/binários `zabbix_*` com `-V`. Nada aqui escreve ou altera o ambiente do
cliente.

## As três fontes de dado e como cada uma chega no relatório

| Fonte | Como coleta | Precisa de quê |
|---|---|---|
| **Zabbix** | API JSON-RPC, remoto | Token de API |
| **MySQL** | Conexão direta ao banco, remoto | Usuário read-only |
| **Infraestrutura** (SO, CPU, RAM, disco, versão do binário Zabbix) | **Não tem API** — roda um script local na máquina do cliente e o resultado (JSON) é lido de uma pasta | SSH manual (você já tem hoje) |
| **Grafana** (opcional, não priorizado) | API HTTP, remoto | API key |

### Fluxo de infraestrutura (a parte que precisa de SSH)

O `probe/flowbix_probe.py` é um script único, **sem dependências** (só usa a
biblioteca padrão do Python — não precisa `pip install` na máquina do
cliente). Ele roda local na máquina, lê SO/CPU/RAM/disco/versão do Zabbix
instalado e imprime **um JSON** no stdout. Esse JSON é o contrato entre "rodar
no cliente" e "aparecer no relatório" — não importa como ele chegou até você.

```
Modo manual (o que você vai usar hoje):

  Você (SSH manual, como já faz)
      │
      ├─▶ zabbix-server-varejo-prd:  python3 flowbix_probe.py > srv.json
      ├─▶ VMPLNX5081:                python3 flowbix_probe.py > proxy1.json
      └─▶ VMPLNX5099:                python3 flowbix_probe.py > proxy2.json
      │
      scp/copia os .json de volta para ./coletas/ (no seu computador)
      │
      ▼
  flowbix-assess run --config configs/cliente.yaml --infra-dir ./coletas/
      │
      ▼
  [coletor lê os JSONs] → [motor de regras] → report.html
```

Isso vale tanto rodando via CLI (`--infra-dir ./coletas`) quanto pelo
front-end web — nesse caso a pasta é `data/clients/<slug>/infra/` em vez de
`./coletas`, mas o mecanismo é o mesmo: o coletor só lê `.json` de uma pasta.

O nome de cada arquivo `.json` não importa — o script lê o campo `"host"` de
dentro do JSON (preenchido automaticamente com o hostname da máquina) para
casar com o `name:` configurado em `infra.hosts` no YAML do cliente (isso é
o que permite cruzar CPU/RAM/disco com o *papel* do host — server ou proxy —
e checar coisas como "porta 10061 deveria estar ouvindo num proxy").

Se no futuro quiser eliminar o passo manual de `scp`, dá para trocar o "você
copia o script e roda" por um orquestrador que abre SSH (Paramiko), sobe o
mesmo `probe.py`, executa e lê o stdout direto — sem tocar em nada além de
onde os JSONs entram no pipeline. Não é prioridade agora, mas a arquitetura
já separa "como o dado chega" (`collectors/infra_local.py` só lê uma pasta)
de "como ele foi coletado", então dá para trocar depois sem reescrever regra
nenhuma.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Front-end web (recomendado para uso do dia a dia)

```bash
python -m webapp.app
```

Abre em `http://127.0.0.1:5050`. Fluxo:

1. Criar um cliente pelo nome (ex.: "Grupo Boticário").
2. Na página do cliente, preencher URL/token do Zabbix e host/usuário/senha
   do MySQL (opcional — pode deixar em branco e rodar só com infra). Isso
   fica salvo em `data/clients/<slug>/config.yaml`.
3. Rodar o probe em cada host via SSH (ver seção "Fluxo de infraestrutura"
   abaixo) e colocar os `.json` gerados em `data/clients/<slug>/infra/` — a
   própria página já mostra quais arquivos ela encontrou ali.
4. Clicar em "Rodar validação" — a página chama Zabbix, MySQL e Infra em
   sequência, mostrando cada etapa (✅ ok / ⏭️ pulado / ❌ erro) em vez de um
   spinner único, e no final gera **dois arquivos**: o relatório em HTML e a
   apresentação final em **PPTX** (pronta para apresentar ao cliente), a
   partir dos mesmos achados.

Cada cliente acumula seu histórico de relatórios gerados (`reports/`) — dá
pra comparar assessments de datas diferentes do mesmo cliente.

**Nota de segurança (rodando direto com `python -m webapp.app`, sem
Docker/Caddy)**: nesse modo é um servidor local (`127.0.0.1`), pensado para
rodar na sua própria máquina. Token/senha ficam em
`data/clients/<slug>/.env` (fora do git); URL, thresholds e datas de
validade em `config.yaml`. Não exponha essa porta em rede sem passar pela
camada de autenticação descrita na seção "Uso centralizado" abaixo.

## Uso centralizado (servidor da empresa)

Para o time todo usar a mesma instância — mesmo histórico de clientes, sem
reinstalar em cada máquina — a ferramenta sobe via Docker num servidor único
administrado pela empresa, atrás de HTTPS + autenticação (Caddy). Fluxo:

1. **No servidor**: clonar/copiar este projeto, criar um arquivo `.env` na
   raiz (ao lado do `docker-compose.yml` — é diferente do `.env` por
   cliente citado acima) com as credenciais de acesso ao próprio painel:
   ```bash
   echo "APP_BASIC_AUTH_USER=equipe" > .env
   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'escolha-uma-senha-forte'
   echo "APP_BASIC_AUTH_HASH=<hash gerado acima>" >> .env
   docker compose up -d
   ```
   Isso sobe dois serviços: o app Flask (só acessível dentro da rede do
   compose) e o Caddy na frente, servindo `https://<ip-ou-host-da-vm>` com
   TLS (certificado próprio — o navegador vai pedir para confiar nele na
   primeira vez) e pedindo usuário/senha antes de mostrar qualquer página.
   Se esse servidor já ficar atrás de VPN/SSO da empresa, o bloco
   `basic_auth` do [Caddyfile](Caddyfile) pode ser removido — mantendo só o
   `tls internal` para criptografar o tráfego.

2. **Por cliente, na página web**: preencher nome, URL do Zabbix (+ token
   e a data de validade dele), MySQL (host/usuário/senha) e, se já tiver,
   Grafana (URL + token — a coleta em si ainda não entra no relatório,
   fica para uma próxima etapa). Tokens/senhas nunca aparecem de volta na
   tela depois de salvos.

3. **Coleta de infraestrutura** (a única parte que ainda depende de SSH):
   na página do cliente, baixar o script de coleta já nomeado para aquele
   cliente, rodar via SSH manual no terminal do cliente (exatamente como na
   seção "Fluxo de infraestrutura" acima — **o servidor central nunca tem
   acesso SSH ao ambiente do cliente**, só o script roda lá e o resultado
   volta por upload):
   ```bash
   ssh usuario@host-do-cliente 'python3 -' < flowbix_probe_<cliente>.py > resultado.json
   ```
   Depois, subir esse `resultado.json` pelo formulário de upload na mesma
   página.

4. **Rodar validação** — igual ao fluxo local: Zabbix → MySQL →
   Infraestrutura → relatório HTML + PPTX. Um token vencido (pela data
   informada no passo 2) faz aquela fonte ser pulada automaticamente, com
   aviso, em vez de tentar chamar a API.

Manter o servidor da empresa atualizado: `git pull && docker compose up -d
--build` (o `docker compose build` reinstala dependências se
`requirements.txt` mudou).

## Uso via linha de comando (sem o front)

Útil para automação/CI ou para quem prefere terminal ao front-end.

### Modo demo (sem credenciais reais)

```bash
python -m flowbix_assess.cli demo --output report_demo.html
```

Gera um relatório com dados de exemplo (`sample_data/`, incluindo
`sample_data/infra/*.json` como se fossem saídas reais do probe) para validar
que o motor de regras e o template estão funcionando.

### Modo real (contra o ambiente de um cliente)

1. Copie `configs/example.yaml` para `configs/<cliente>.yaml` e preencha:
   - `zabbix.url` + `zabbix.token` (gere em Administration > API tokens)
   - `mysql.host` + `mysql.user`/`password` (usuário **read-only**)
   - `infra.hosts` — nome de cada host (server/proxies) e seu papel
2. Rode o probe em cada máquina via SSH e traga os JSONs de volta:
   ```bash
   ssh usuario@zabbix-server-varejo-prd 'python3 -' < probe/flowbix_probe.py > coletas/srv.json
   ssh usuario@VMPLNX5081 'python3 -' < probe/flowbix_probe.py > coletas/proxy1.json
   ssh usuario@VMPLNX5099 'python3 -' < probe/flowbix_probe.py > coletas/proxy2.json
   ```
   (o truque `python3 - < arquivo` executa o script sem precisar fazer `scp`
   dele antes; a saída já vem redirecionada para o arquivo local)
3. Nunca coloque senha/token direto no YAML — use `${VAR}`:
   ```bash
   export ZABBIX_API_TOKEN=xxxx
   export MYSQL_PASSWORD=xxxx
   python -m flowbix_assess.cli run --config configs/cliente.yaml --infra-dir ./coletas \
     --output report.html --pptx apresentacao.pptx
   ```
4. Abra o `.html` no navegador ou o `.pptx` no PowerPoint/Keynote/Google Slides.

Se uma fonte falhar ou não estiver configurada, o relatório continua sendo
gerado com as fontes disponíveis e lista no topo quais fontes ficaram de
fora.

## Saída: HTML + PPTX

Toda execução (CLI ou front-end) gera os achados uma vez e os renderiza em
dois formatos a partir da mesma lista: um relatório HTML (`report.py`) e uma
apresentação pronta em PPTX (`pptx_report.py`) — título, resumo executivo,
agenda e um slide por achado, para levar direto pro cliente. É um template
genérico (sem logo/identidade visual); se a Flowbix tiver um `.pptx` oficial
de marca, dá pra adaptar `pptx_report.py` para carregar esse arquivo como
base em vez de criar uma apresentação em branco.

## Ambiente: nuvem, on-premise ou híbrido

O motor de regras não assume AWS/nuvem — SO, CPU/RAM/disco e as checagens de
Zabbix funcionam igual em qualquer ambiente, porque vêm do probe local e da
API do Zabbix. O único achado que muda de texto conforme o ambiente é o de
alta disponibilidade/custo do banco (`client.hosting_type: cloud|on-premise|
hybrid` no config, ou o campo "Tipo de ambiente" no front-end) — sem isso
preenchido, ele usa uma redação genérica que não presume provedor nenhum.

## O que já é automatizado (v1)

Cobertura direta do levantamento completo de infra + Zabbix:

**Arquitetura do Ambiente** — resumo da topologia (versão do server, proxies);
criptografia PSK dos proxies; forma de acesso ao ambiente (SSH/VPN/cofre de
senhas) fica como Revisão Manual — é política/rede, não algo exposto pela API.

**Processamento das Máquinas** — utilização de CPU do server/proxies (via
histórico de itens self-monitoring); utilização dos processos internos do
Zabbix (poller, syncer, etc.) com recomendação de qual parâmetro
(`StartPollers`, `StartDBSyncers`...) ajustar quando sobrecarregado.

**Infraestrutura** — compatibilidade de SO vs. versão-alvo do Zabbix (matriz
configurável, com sugestão de caminho de upgrade incremental); carga de CPU,
uso de memória e espaço em disco por host; divergência de versão entre
binários `zabbix_server`/`zabbix_proxy` instalados e a versão reportada pela
API; portas esperadas por papel (server/proxy) não detectadas ouvindo.

**Banco de Dados (MySQL)** — versão próxima do fim de suporte; compatibilidade
mínima de versão com o Zabbix-alvo; tamanho total e por tabela; análise de
crescimento mensal (acumula histórico entre execuções via front-end); tabelas
de histórico sem particionamento; idade de dados antigos vs retenção
configurada (opcional, pode ser lento — ver `mysql.check_history_age`);
parâmetros de tuning (`max_connections`, `event_scheduler`,
`innodb_io_capacity`); política de backup fica como Revisão Manual (não dá
pra confirmar frequência/retenção real só com uma conexão de leitura).

**Análise de Templates** — itens master com histórico duplicado nos
dependentes; itens com intervalo de coleta curto (geral, e o subconjunto de
baixa prioridade sem trigger); itens com conexão direta ao banco via ODBC
(e se estão gerando erro de conexão); excesso de pré-processamento por item;
excesso de discovery rules por template; lógica de triggers somando itens
(heurística) em vez de `min()`/`max()`; itens `unsupported` (contagem e
causas mais frequentes, por amostragem).

**Análise de Mídias** — media types com nomes sinalizados como
JS-deprecated (lista configurável).

**Grafana (opcional, não priorizado)** — versão desatualizada; dashboards com
nome sugestivo de teste. Só roda se `grafana:` estiver no config.

## O que fica como Revisão Manual (documentado, não escondido)

Alguns itens do levantamento não têm como ser confirmados só com API/SSH/MySQL
— aparecem no relatório como achado `Revisão Manual` em vez de simplesmente
sumirem:

- Custo de infraestrutura em nuvem/on-premise (Multi-AZ, reserva de
  instância, licenciamento) — precisaria da API de billing do provedor.
- Forma de acesso ao ambiente (VPN, cofre de senhas) — é política/processo,
  não configuração visível via API.
- Política de backup e existência de backup estrutural — não dá pra
  confirmar com certeza só via `SHOW` no MySQL.
- Orquestração automática do SSH (hoje é manual — ver seção de fluxo acima).
- Inspeção profunda de dashboards do Grafana (queries sem filtro de tempo,
  excesso de rows/painéis) — precisa baixar o JSON model de cada dashboard.
- Detecção mais precisa de objetos JavaScript deprecados em media types
  (v1 usa uma lista de nomes configurável, não faz parsing do script).
- Print de tela das validações do Zabbix para anexar ao PPT — em avaliação
  (depende de login no frontend, não só o token de API).

## Estrutura

```
probe/
  flowbix_probe.py    # script standalone (roda no cliente via SSH)
  collect_all.sh       # helper: roda o probe em vários hosts de uma vez
webapp/
  app.py                # front-end Flask (credenciais + validação passo a passo)
  templates/, static/    # HTML/CSS do front
flowbix_assess/
  collectors/     # Zabbix API, MySQL, leitura dos JSONs de infra, Grafana API
  rules/          # motor de regras por fonte, thresholds vêm do config
  clientstore.py  # layout de pastas por cliente (usado pelo front)
  config.py       # carrega YAML com interpolação de ${ENV_VAR}
  report.py       # renderiza o HTML final (Jinja2)
  cli.py          # `run` (ambiente real) e `demo` (dados de exemplo)
templates/        # template do relatório HTML
configs/           # config de exemplo para uso via CLI (não commitar credenciais)
data/clients/      # por cliente: config.yaml, infra/, reports/ (gitignored)
sample_data/       # fixtures para o modo demo (inclui sample_data/infra/)
tests/             # smoke tests do motor de regras contra os fixtures
```

## Testes

```bash
python -m pytest tests/ -q
```
