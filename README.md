# Arkanis no Discord

Bot em português para **um servidor**. Já vem com o calendário revisado de Arkanis. O ponto de partida é **1 de Arkanis (mês 1), ano 1, às 00:00**, com 26 meses (incluindo **Skaryel**), semanas de 12 dias, três luas, três sóis e quatro estações.

## Instalar

1. Crie um aplicativo dedicado no [Portal de Desenvolvedores do Discord](https://discord.com/developers/applications). Na seção **Bot**, copie ou gere um token. Guarde o token no seu computador, nunca em mensagens nem no GitHub.
2. Em **Installation**, configure **Guild Install** com os escopos `bot` e `applications.commands`. Para o canal dos avisos, o bot precisa de **Ver canal**, **Enviar mensagens** e **Inserir links**. Habilite também **Anexar arquivos** no canal em que quiser usar a exportação. O bot não precisa do cargo Administrador nem da opção de ler conteúdo de mensagens. Use o link de instalação para adicioná-lo ao seu servidor.
3. Em **Configurações do usuário → Avançado** no Discord, habilite **Modo desenvolvedor**. Depois use **Copiar ID do servidor** no menu do servidor.
4. No Linux ou macOS, abra o terminal na pasta extraída. É necessário Python 3.11 ou superior.

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   cp .env.example .env
   chmod 600 .env
   ```

5. Abra o `.env` e preencha `DISCORD_TOKEN` e `DISCORD_GUILD_ID`. Em seguida:

   ```sh
   .venv/bin/python bot.py
   ```

6. Execute `/calendario hoje` no servidor. Um administrador pode usar `/arkanis-admin configuracoes` para abrir o painel interativo. Para ativar a rotina, escolha **Rotina diária**, selecione o canal e clique em **Configurar e ativar**. O padrão é **00:00 no fuso America/Fortaleza**.

Os comandos são registrados apenas no servidor cujo ID está no `.env`. Use um aplicativo dedicado, pois a sincronização substitui os comandos desse aplicativo nesse servidor. Não configure um **Interactions Endpoint URL** no Portal. Este bot recebe as interações pela conexão com o Discord. Para atualizar a data e postar mesmo com o laptop fechado, mantenha o processo em uma máquina ou hospedagem ligada o tempo todo.

## Atualizar da primeira versão

Pare o bot, conserve o `.env` e o banco em `runtime/arkanis.sqlite3` (ou no caminho definido em `ARKANIS_DB`), faça uma cópia do banco e substitua os arquivos do programa pelo conteúdo deste pacote. Inicie novamente com o mesmo comando. Na primeira abertura, a migração troca **Syrveth** por **Skaryel** sem alterar o índice do mês, substitui o **Sol principal** provisório pelos três sóis e mantém datas, eventos, outras edições e a rotina que já estiver configurada. Sóis manuais personalizados da versão anterior são preservados.

O novo ponto de partida é usado ao criar o banco pela primeira vez. Para ajustar uma instalação existente, um ADM pode executar `/arkanis-admin data-definir dia:1 mes:1 ano:1 hora:00:00` sem apagar os eventos.

## Comandos para membros

| Comando | O que mostra |
|---|---|
| `/calendario hoje` | Data atual, dia da semana, estação, fases das luas e eventos. |
| `/calendario dia` | Uma data específica. |
| `/calendario semana` | A semana da data, incluindo dias do mês vizinho, se houver. |
| `/calendario mes` | O mês em lista, com dias especiais e eventos. As listas longas têm botões de navegação. |
| `/calendario ano` | Duração do ano, lista numerada de meses e ordem dos dias da semana. |
| `/eventos hoje`, `dia`, `agenda`, `detalhe` | Eventos publicados, agenda de até 120 dias e descrição por ID. |
| `/ceu luas` | Fases das luas da data escolhida. |
| `/ceu sois` | Mostra quais sóis aparecem naquele dia e quais estão acima do horizonte naquele horário. Também mostra nascer, pôr, altura no céu e ciclo orbital. |
| `/ceu estacoes` | Estação atual e início das quatro estações. |

Os campos de mês aceitam nome ou número. Datas futuras podem ser consultadas no bot. Em caso de spoiler, marque o evento como oculto para ficar fora de listas e notificações públicas.

## Comandos ADM

Só quem tem a permissão **Administrador** no servidor consegue executar os comandos de `/arkanis-admin`. As respostas de administração só aparecem para quem executou.

| Subcomandos | Função |
|---|---|
| `configuracoes` | Abre o painel interativo privado, com seletores e formulários para céu, calendário, eventos e rotina. |
| `evento-criar`, `evento-editar`, `evento-remover`, `eventos-listar` | Criam eventos de data fixa, editam, ocultam, apagam ou listam eventos e IDs. Os quatro eventos de início de estação do arquivo continuam recorrentes. |
| `mes-adicionar`, `mes-editar` | Criam, inserem antes de outro mês, renomeiam ou mudam a duração. Ao mudar a estrutura, o bot mantém os eventos vinculados aos seus meses e recalcula as estações. |
| `dia-adicionar`, `dia-editar` | Criam e renomeiam dias especiais. Cada dia adicionado entra no fim do mês e ocorre todo ano. Dias comuns são redimensionados pelo comando do mês. |
| `semana-adicionar`, `semana-editar` | Adicionam ou renomeiam um dia da semana. |
| `data-definir`, `data-mover` | Acertam a data, alteram a hora fictícia ou avançam/voltam um número de dias. |
| `sol-adicionar`, `sol-editar`, `sol-remover`, `ceu-latitude` | Ajustam os sóis e o ponto de observação. O período dos sóis é calculado a partir do semieixo. |
| `diario`, `pausar` | Ativam ou pausam o relógio diário e os avisos. |
| `exportar` | Baixa `arkanis.json` no formato do arquivo original do Fantasy Calendar, com configuração, data, luas, estações e eventos. Inclui os eventos ocultos. |
| `backup` | Baixa um JSON de referência do estado completo do bot, incluindo sóis e avisos. |

### Os três sóis

| Sol | Porte relativo | Semieixo relativo | Período inicial |
|---|---:|---:|---:|
| Innart | 0,42 | 0,36 | 217,73 dias |
| Dinart | 0,72 | 0,65 | 528,24 dias |
| Reart | 1,00 | 1,00 | 1.008 dias |

O modelo usa órbitas circulares independentes: o período é proporcional ao semieixo elevado a 3/2, pela terceira lei de Kepler no modelo gravitacional de Newton. Fase e inclinação mudam a trajetória aparente. A latitude do observador (48° por padrão) determina quando a trajetória fica acima do horizonte. É uma aproximação para o céu de Arkanis, sem simulação de interação gravitacional entre as três estrelas. [Referência sobre órbitas e gravidade](https://science.nasa.gov/learn/basics-of-space-flight/chapter3-3/).

Reart é a referência do relógio solar: sua passagem mais alta ocorre às **16:00**. A latitude e a inclinação de Reart são validadas para garantir que ele nasça em qualquer dia. Innart e Dinart têm dias em que não nascem e dias em que ficam acima do horizonte o tempo todo. Há noites normalmente. No painel **Céu**, selecione uma estrela ou lua para editar, ou escolha **Latitude**. Os campos de órbita, inclinação, fase e tamanho aceitam ponto ou vírgula decimal.

O relógio de **Arkanis** tem 32 horas e 64 minutos por hora. Os horários da **rotina diária** são reais, com 24 horas e 60 minutos por hora.

O modelo de sóis e a rotina do servidor são recursos próprios do bot e ficam fora do JSON do Fantasy Calendar. O bot não edita o calendário hospedado no Fantasy Calendar automaticamente.

### Como o relógio diário funciona

- Fica desligado até alguém configurar `diario`. Não avança na hora da configuração. O primeiro avanço é no próximo horário agendado.
- Cada dia real vencido avança **um dia fictício**. No mesmo aviso, o bot pode publicar a data e os eventos daquele dia. Dá para desligar cada tipo de aviso separadamente.
- Depois de ficar offline, o bot recupera os dias perdidos e posta só o aviso da data mais recente. O horário fictício não muda com o horário real.
- A data é gravada antes de tentar enviar. Se o Discord estiver indisponível, o envio é tentado de novo; o dia não avança duas vezes. Se o processo cair entre o envio e a confirmação local, o último aviso pode ser repetido.

O estado fica em `runtime/arkanis.sqlite3`. Rode uma instância por vez. Para migrar ou restaurar, pare o bot e copie o arquivo SQLite para a instalação de destino, guardando também os valores de configuração do `.env` de forma privada. Editar o JSON em `data/` não altera um banco já inicializado.

## Rodar os testes

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Os testes cobrem a virada do ano, a migração do banco, os ciclos do céu, os 1.008 dias do primeiro ano, eventos ocultos e a recuperação do avanço diário. A suíte não exige token do Discord. Para conferir a instalação ao vivo, teste `/calendario hoje` com um membro e `/arkanis-admin diario` com um administrador no servidor configurado.
