# -*- coding: utf-8 -*-
"""
Auto Farm Fortnite (PC)

Lê a tela com OCR (RapidOCR, roda offline), acha os botões pelo texto e clica/aperta teclas.
Mesmas funções da versão Android:
  - sair quando o esquadrão morrer (Return to lobby) e puxar outra partida
  - sair e puxar por tempo
  - anti-AFK
  - clicar em CONTINUAR
  - garantir o tamanho de equipe Esquadrão no lobby

Atalho global: F6 (iniciar/pausar).
"""
import json
import os
import queue
import sys
import threading
import time
import traceback
import unicodedata
from dataclasses import dataclass

IS_WIN = sys.platform == "win32"
APP_NAME = "AutoFarmFortnite"
GAME_TITLE = "Fortnite"          # título da janela do jogo (sem espaços nas pontas)

# ----------------------------------------------------------------------------
# Textos que o app procura na tela (sem acento, separados por vírgula).
# Dá para editar em "Configurações avançadas".
# ----------------------------------------------------------------------------
DEFAULT_KEYS = {
    "play": "JOGAR,INICIAR,PLAY",
    "searching": "LOCALIZANDO,PROCURANDO,CANCELAR,LOCATING,FINDING,CANCEL",
    "dead": "ESQUADRAO ELIMINADO,EQUIPE ELIMINADA,RESUMO DA PARTIDA,ESTATISTICAS DA PARTIDA,VOCE FICOU EM,VOCE TERMINOU EM,SQUAD ELIMINATED,TEAM ELIMINATED,MATCH RECAP,YOU PLACED",
    "recap": "RESUMO DA PARTIDA,ESTATISTICAS DA PARTIDA,VOCE FICOU EM,VOCE TERMINOU EM,MATCH RECAP,YOU PLACED,MATCH STATS",
    "again": "JOGAR NOVAMENTE,JOGAR DE NOVO,PLAY AGAIN",
    "cont": "CONTINUAR,CONTINUE",
    "exit": "CONFIRMAR,VOLTAR AO LOBBY,RETORNAR AO LOBBY,SAIR PARA O LOBBY,VOLTAR PARA O LOBBY,RETORNAR PARA O LOBBY,SAIR DA PARTIDA,ABANDONAR PARTIDA,SAIR,CONFIRM,RETURN TO LOBBY,LEAVE MATCH,LEAVE",
    "mode_squad": "ESQUADRAO,ESQUADRA,ESQUADR,SQUAD",
    "mode_size": "SOLO,DUPLA,DUPLAS,DUO,DUOS,TRIO,TRIOS,ESQUADRAO,ESQUADR,SQUAD",
    "mode_card": "TROCACAO,CONSTRUCAO,SOLO,DUPLA,DUPLAS,TRIO,TRIOS,ESQUADRAO,PREENCH,COMPLETAR,FILL,BUILD",
    "mode_cfg": "MODO RANQUEADO,TAMANHO DA,PREENCHER EQUIPE,COMPLETAR EQUIPE,PREENCHIMENTO DE EQUIPE,RANKED,TEAM FILL,GIZMOS,TEAM SIZE",
    "mode_done": "CONCLUIDO,PRONTO,CONCLUIR,DONE",
    # Tesouros: "ACESSO DESBLOQUEADO À LOJA PIRATA" (RESGATAR TUDO) e "TESOURO ENCONTRADO" (RESGATAR)
    "claim_all": "RESGATAR TUDO,RESGATAR TODOS,CLAIM ALL",
    "claim": "RESGATAR,CLAIM",
    "treasure": "TESOURO ENCONTRADO,ACESSO DESBLOQUEADO,TREASURE FOUND,ACCESS UNLOCKED",
    "pirate": "LOJA PIRATA,PIRATE STORE,PIRATE SHOP",
}
EXTRA_KEYS = {   # sempre somados aos textos salvos (valem mesmo para quem já salvou os campos)
    "exit": "VOLTAR AO LOBBY,RETORNAR AO LOBBY,SAIR PARA O LOBBY,VOLTAR PARA O LOBBY,RETORNAR PARA O LOBBY,SAIR DA PARTIDA",
    "again": "JOGAR NOVAMENTE,JOGAR DE NOVO",
    "recap": "RESUMO DA PARTIDA,ESTATISTICAS DA PARTIDA,VOCE FICOU EM,VOCE TERMINOU EM",
    "mode_done": "CONCLUIDO,PRONTO,CONCLUIR",
}
# Campos editáveis na tela (rótulo)
KEY_LABELS = {
    "play": "Botão JOGAR do lobby",
    "searching": "Procurando partida",
    "dead": "Esquadrão eliminado / resumo",
    "recap": "Tela de resumo da partida",
    "again": "Botão Jogar novamente",
    "cont": "Botão CONTINUAR",
    "exit": "Sair (Return to lobby / confirmar)",
    "mode_squad": "Tamanho Esquadrão",
    "mode_card": "Resumo do modo no lobby",
    "mode_cfg": "Tela de configuração do modo",
    "mode_done": "Botão DONE da configuração",
}

# Mostrar o editor de textos em "Configurações avançadas" (os padrões já estão certos)
SHOW_TEXT_EDITOR = False

# Tempos (segundos)
AFK_S = 30.0
BEAT_S = 45.0
PLAY_COOLDOWN_S = 15.0
LEAVE_TIMEOUT_S = 60.0
# Textos que nunca devem ser clicados ao sair da partida (fechariam o jogo inteiro)
EXIT_BLOCK = ("QUIT", "SAIR DO JOGO", "EXIT GAME", "DESKTOP", "AREA DE TRABALHO")
AFTER_EXIT_WAIT_S = 8.0
BURGER_COOLDOWN_S = 1.5       # entre cliques nas 3 barrinhas
CLAIM_COOLDOWN_S = 3.0        # entre toques em RESGATAR
NOTIFY_COOLDOWN_S = 20.0      # mínimo entre avisos de tesouro (evita repetir se o clique falhar)
MODE_COOLDOWN_S = 1.8
MODE_MAX_TRIES = 3
# Ajustes do clique no ícone de Esquadrão, em fração da altura da tela, tentados em ordem até o texto
# da tela mostrar "Esquadrão". O clique costuma cair à esquerda do ícone, então tenta primeiro para a direita.
ICON_SWEEP = (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, -0.02, -0.04)
MENU_COOLDOWN_S = 1.8

# Posições de reserva (fração da tela) usadas só quando o OCR não acha o texto
RECAP_EXIT_X, RECAP_EXIT_Y = 0.865, 0.747   # botão RETURN TO LOBBY da tela de resumo
MODE_CARD_X, MODE_CARD_Y = 0.114, 0.729     # resumo do modo no lobby
SQUAD_X, SQUAD_Y = 0.820, 0.392             # ícone de Esquadrão na configuração
DONE_X, DONE_Y = 0.796, 0.892               # botão DONE da configuração

# Teclas (scancodes)
SC_ESC = 0x01
SC_W = 0x11


# ----------------------------------------------------------------------------
# Utilidades de texto
# ----------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper()


def lev(a, b):
    dp = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, len(b) + 1):
            tmp = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (0 if a[i - 1] == b[j - 1] else 1))
            prev = tmp
    return dp[len(b)]


def has_word(text, key):
    """Palavra inteira: evita que PLAY case com PLAYERS."""
    i = text.find(key)
    while i >= 0:
        before = text[i - 1] if i > 0 else " "
        j = i + len(key)
        after = text[j] if j < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = text.find(key, i + 1)
    return False


def has_key(text, key, fuzzy):
    if has_word(text, key):
        return True
    if not fuzzy or len(key) < 7:
        return False
    t = text.replace(" ", "")
    k = key.replace(" ", "")
    if k in t:
        return True
    max_d = 2 if len(k) >= 12 else 1
    for ln in range(len(k) - 1, len(k) + 2):
        if ln > len(t) or ln <= 0:
            continue
        for s in range(0, len(t) - ln + 1):
            if lev(t[s:s + ln], k) <= max_d:
                return True
    return False


def ocr_scale(w, ocr_width):
    """Escala do OCR: reduz telas grandes e AMPLIA janelas pequenas (texto miúdo não é lido direito)."""
    if not w:
        return 1.0
    if w >= ocr_width:
        return float(ocr_width) / float(w)
    return min(3.0, max(1.0, 1600.0 / float(w)))


def find_hamburgers(img):
    """Acha ícones de 3 barras horizontais brancas (botão de menu) no canto superior direito.
    Usa só a imagem (nada de posição estimada). Devolve [(cx, cy, largura)] da direita para a esquerda."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    h, w = img.shape[:2]
    x0, y1 = int(w * 0.50), int(h * 0.55)
    roi = img[0:y1, x0:w]
    mx = roi.max(axis=2).astype(np.int16)
    mn = roi.min(axis=2).astype(np.int16)
    mask = ((mn > 205) & ((mx - mn) < 50)).astype(np.uint8)       # branco quase puro
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask, connectivity=8)
    bars = []
    for i in range(1, n):
        x, y, bw, bh, area = [int(v) for v in stats[i]]
        if not (0.010 * h <= bw <= 0.075 * h):
            continue
        if not (max(2, 0.003 * h) <= bh <= 0.024 * h):
            continue
        if bw < 2.5 * bh or area < 0.6 * bw * bh:                  # barra cheia e comprida
            continue
        bars.append((x, y, bw, bh))
    out = []
    for a in bars:
        col = sorted((b for b in bars if b[1] > a[1] and abs(b[0] - a[0]) <= 0.2 * a[2]
                      and abs(b[2] - a[2]) <= 0.2 * a[2]), key=lambda b: b[1])
        for b in col:
            gap = b[1] - a[1]
            if gap < 0.9 * a[3] or gap > 5 * a[3] + 0.02 * h:
                continue
            c = next((c for c in col if c[1] > b[1] and abs((c[1] - b[1]) - gap) <= 0.35 * gap), None)
            if c is not None:
                out.append((x0 + a[0] + a[2] / 2.0, b[1] + b[3] / 2.0, float(a[2])))
                break
    out.sort(key=lambda t: -t[0])
    ded = []
    for t in out:
        if not any(abs(t[0] - d[0]) < 0.02 * h and abs(t[1] - d[1]) < 0.02 * h for d in ded):
            ded.append(t)
    return ded


@dataclass
class Hit:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self):
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self):
        return (self.y1 + self.y2) / 2.0

    @property
    def area(self):
        return max(1.0, (self.x2 - self.x1) * (self.y2 - self.y1))


# ----------------------------------------------------------------------------
# Configurações
# ----------------------------------------------------------------------------
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        folder = app_dir()
    return os.path.join(folder, "config.json")


class Settings:
    BOOLS = ("dead", "by_time", "afk", "cont", "mode", "minimize")

    def __init__(self):
        self.dead = True        # sair quando o esquadrão morrer
        self.by_time = True     # sair e puxar por tempo
        self.afk = True         # anti-AFK
        self.cont = True        # clicar em CONTINUAR
        self.mode = True        # garantir Esquadrão no lobby
        self.minimize = True    # minimizar este app ao iniciar
        self.minutes = 6.5
        self.tick = 2.0         # segundos entre leituras da tela
        self.ocr_width = 1280   # largura usada no OCR (menor = mais rápido)
        self.capture = "auto"   # auto = janela do Fortnite; monitor = tela inteira
        self.monitor = 1
        self.icon_dx = 0.0      # ajuste aprendido do clique no ícone de Esquadrão (fração da altura)
        self.ntfy = ""          # tópico do ntfy.sh para avisar no celular (vazio = só som/log)
        self.keys = {}

    def load(self):
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return
        for k in self.BOOLS:
            if k in d:
                setattr(self, k, bool(d[k]))
        for k, cast in (("minutes", float), ("tick", float), ("ocr_width", int), ("monitor", int), ("icon_dx", float)):
            if k in d:
                try:
                    setattr(self, k, cast(d[k]))
                except Exception:
                    pass
        if isinstance(d.get("ntfy"), str):
            self.ntfy = d["ntfy"].strip()
        if d.get("capture") in ("auto", "monitor"):
            self.capture = d["capture"]
        if isinstance(d.get("keys"), dict):
            self.keys = {str(k): str(v) for k, v in d["keys"].items()}

    def save(self):
        d = {k: getattr(self, k) for k in self.BOOLS}
        d.update(minutes=self.minutes, tick=self.tick, ocr_width=self.ocr_width,
                 capture=self.capture, monitor=self.monitor, icon_dx=self.icon_dx, ntfy=self.ntfy, keys=self.keys)
        try:
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def keys_for(self, name):
        raw = (self.keys.get(name) or DEFAULT_KEYS.get(name, "")) + "," + EXTRA_KEYS.get(name, "")
        out = []
        for k in raw.split(","):
            k = norm(k.strip())
            if k and k not in out:
                out.append(k)
        return out


# ----------------------------------------------------------------------------
# Lógica do bot (sem dependência de tela/Windows: dá para testar sozinha)
# ----------------------------------------------------------------------------
class Bot:
    def __init__(self, cfg, io, log, on_stop=None):
        self.cfg = cfg
        self.io = io
        self.log = log
        self.on_stop = on_stop or (lambda why: None)
        self.notify = lambda title, body, urgent=False: None   # o Worker troca por um aviso de verdade
        self.matches = 0
        try:
            self.icon_dx = float(getattr(cfg, "icon_dx", 0.0) or 0.0)
        except Exception:
            self.icon_dx = 0.0
        self.reset(time.time())

    def reset(self, now):
        self.leaving = False
        self.leave_start = 0.0
        self.leave_step = 0
        self.last_menu = 0.0
        self.last_exit = 0.0
        self.last_queue = now
        self.last_afk = now
        self.last_beat = now
        self.last_play = 0.0
        self.last_mode = 0.0
        self.mode_tries = 0
        self.mode_cfg_taps = 0
        self.icon_prev = None
        self.icon_start = None
        self._icon_src = ""
        self.exit_tried = set()
        self.burger = []             # ícones de 3 barrinhas achados na imagem (o Worker preenche)
        self.burger_tried = set()
        self.last_burger = 0.0
        self.treasures = 0
        self.last_treasure = ""
        self._last_claim_name = ""
        self.done_miss = 0
        self.last_claim = 0.0
        self.last_notify = 0.0
        self.want_debug = False
        self.menu_debug_done = False
        self.recap_waits = 0
        self.last_state = ""

    def seconds_to_exit(self, now):
        if not self.cfg.by_time or self.leaving:
            return None
        return max(0, int(self.cfg.minutes * 60 - (now - self.last_queue)))

    def _state(self, s, seen):
        if s != self.last_state:
            self.last_state = s
            self.log("estado: %s | lido: %s" % (s, seen))

    def _start_leaving(self, why, now):
        self.leaving = True
        self.leave_start = now
        self.leave_step = 0
        self.last_menu = 0.0
        self.last_exit = 0.0
        self.recap_waits = 0
        self.exit_tried = set()
        self.burger_tried = set()
        self.last_burger = 0.0
        self.log("saindo da partida (%s)" % why)

    def handle(self, items, w, h, now, seen=""):
        cfg = self.cfg

        def match(name, lst, min_y=0.0, skip=None):
            fuzzy = name in ("exit", "mode_cfg", "claim_all", "treasure", "pirate")
            sub = name in ("mode_squad", "mode_card", "mode_size")
            for k in cfg.keys_for(name):
                m = [it for it in lst
                     if it.cy >= min_y and ((k in it.text) if sub else has_key(it.text, k, fuzzy))]
                if name == "exit":
                    # botões têm texto curto; frases de aviso ("...se você sair da partida atual...") não são botões
                    m = [it for it in m if len(it.text) <= 30 and not any(b in it.text for b in EXIT_BLOCK)]
                if skip:
                    m = [it for it in m if (int(it.cx // 15), int(it.cy // 15)) not in skip]
                if m:
                    return min(m, key=lambda it: it.area)
            return None

        def find(name, min_y=0.0, skip=None):
            return match(name, items, min_y, skip)

        def count_keys(name):
            """Quantos textos diferentes da lista aparecem na tela (para telas com vários rótulos)."""
            fuzzy = name in ("exit", "mode_cfg", "claim_all", "treasure", "pirate")
            n = 0
            for k in cfg.keys_for(name):
                if any(has_key(it.text, k, fuzzy) for it in items):
                    n += 1
            return n

        def press(hit, what):
            self.log("clique: %s [%s] em (%d,%d) de %dx%d" % (what, hit.text, hit.cx, hit.cy, w, h))
            self.io.click(hit.cx, hit.cy)

        # Batimento no log
        if now - self.last_beat > BEAT_S:
            self.last_beat = now
            left = int(cfg.minutes * 60 - (now - self.last_queue))
            self.log("[%s] faltam %ds | lido: %s" % (self.last_state, left, seen))

        # 0) Tesouros.
        #    Token da Loja Pirata (RESGATAR TUDO): NÃO clica; pausa o farm e avisa.
        #    Outros tesouros (RESGATAR): avisa qual foi, resgata e continua puxando partidas.
        claim_all = find("claim_all")
        claim_one = match("claim", [it for it in items if "TUDO" not in it.text and "ALL" not in it.text])
        pirate = find("pirate") is not None
        header = find("treasure") is not None
        if claim_all is not None or (pirate and (header or claim_one is not None)):
            self._state("Loja Pirata", seen)
            self.notify("LOJA PIRATA desbloqueada!",
                        "Apareceu o token da Loja Pirata. O farm foi pausado: resgate você mesmo.", True)
            self.on_stop("LOJA PIRATA! Resgate o token você mesmo e depois inicie o farm de novo.")
            return
        if claim_one is not None:
            bottom = 0.35 * w < claim_one.cx < 0.65 * w and claim_one.cy > 0.78 * h   # RESGATAR embaixo, no centro
            if header or bottom:
                self._state("tesouro", seen)
                if now - self.last_claim < CLAIM_COOLDOWN_S:
                    return
                self.last_claim = now
                name = self._treasure_name(items, w, h)
                if name != self._last_claim_name or now - self.last_notify > NOTIFY_COOLDOWN_S:
                    self._last_claim_name = name
                    self.last_notify = now
                    self.treasures += 1
                    self.last_treasure = name
                    self.log("TESOURO RESGATADO: %s" % name)
                    self.notify("Tesouro resgatado", name, False)
                press(claim_one, "resgatar tesouro #%d" % self.treasures)
                return

        # Lobby de verdade: botão JOGAR na coluna da esquerda
        left_items = [it for it in items if it.cx < w * 0.30]
        lobby_play = match("play", left_items, h * 0.35)
        cfg_screen = count_keys("mode_cfg") >= 2     # tela de configuração do modo (vários rótulos)

        # 1) Esquadrão morreu / tela de resumo -> começa a sair
        if not self.leaving and cfg.dead and find("dead") is not None:
            self._start_leaving("esquadrão eliminado", now)

        # 2) Acabou o tempo -> sai (a menos que já esteja no lobby ou configurando o modo)
        time_up = cfg.by_time and (now - self.last_queue) > cfg.minutes * 60
        if not self.leaving and time_up and lobby_play is None and not cfg_screen:
            self._start_leaving("tempo", now)

        # 3) Saindo da partida
        if self.leaving:
            ex = find("exit", skip=self.exit_tried)
            if ex is None and self.exit_tried and find("exit") is not None:
                self.exit_tried.clear()         # já tentou todos os candidatos: recomeça
                ex = find("exit")
            if ex is None and cfg.cont and self.leave_step == 0:
                ex = find("cont", skip=self.exit_tried)   # no máximo 1 vez por tentativa (no menu do ESC, "Continuar" fecha o menu)
            if now - self.leave_start > LEAVE_TIMEOUT_S:
                self._start_leaving("nova tentativa", now)
                return
            elif ex is None and lobby_play is not None:
                self.leaving = False            # chegou no lobby
            else:
                if ex is not None:
                    self.last_exit = now
                    self.recap_waits = 0
                    self.exit_tried.add((int(ex.cx // 15), int(ex.cy // 15)))
                    press(ex, "sair")
                    return
                if now - self.last_exit < AFTER_EXIT_WAIT_S:
                    return                      # espera o lobby carregar
                if find("recap") is not None:
                    # Tela de resumo: só clica no que o OCR leu (Return to lobby; ou Play again como alternativa)
                    self.recap_waits += 1
                    if self.recap_waits >= 3:
                        self.recap_waits = 0
                        ag = find("again")
                        if ag is not None:
                            self.leaving = False
                            self.matches += 1
                            self.last_queue = now
                            self.last_play = now
                            press(ag, "jogar novamente (não li o Return to lobby) #%d" % self.matches)
                        else:
                            self.log("não li Return to lobby nem Play again | lido: %s" % seen)
                            if not self.menu_debug_done:
                                self.menu_debug_done = True
                                self.want_debug = True
                    return
                # 3 barrinhas: ícone sem texto, achado na imagem (nunca por posição estimada).
                # Só depois de abrir o menu com ESC; cada ícone é clicado uma vez por abertura do menu.
                if self.leave_step >= 1 and self.burger and now - self.last_burger > BURGER_COOLDOWN_S:
                    for (bx, by, bsz) in self.burger:
                        key = (int(bx // 15), int(by // 15))
                        if key in self.burger_tried:
                            continue
                        self.burger_tried.add(key)
                        self.last_burger = now
                        press(Hit("3 BARRINHAS", bx - bsz / 2, by - bsz / 2, bx + bsz / 2, by + bsz / 2),
                              "3 barrinhas (achadas na imagem)")
                        return
                if now - self.last_menu > MENU_COOLDOWN_S:
                    self.last_menu = now
                    if self.leave_step == 0:
                        self.burger_tried = set()
                        self.log("tecla: ESC (abre o menu) | lido: %s" % seen)
                        self.io.key(SC_ESC, 0.12)
                        self.leave_step = 1
                    elif self.leave_step in (1, 2):
                        self.leave_step += 1     # espera o OCR achar o botão de sair
                        self.log("menu aberto? procurando o botão de sair | lido: %s" % seen)
                    else:
                        self.log("não achei o botão de sair, ESC de novo (fecha o menu e tenta outra vez) | lido: %s" % seen)
                        if not self.menu_debug_done:
                            self.menu_debug_done = True
                            self.want_debug = True
                        self.io.key(SC_ESC, 0.12)
                        self.leave_step = 0
                return

        # 4) Tela de eliminação: CONTINUAR
        if cfg.cont:
            c = find("cont")
            if c is not None:
                press(c, "CONTINUAR")
                return

        # 5) Procurando partida: espera
        if find("searching") is not None:
            self._state("procurando partida", seen)
            return

        # 5b) Tamanho da equipe: precisa ser Esquadrão
        if cfg.mode:
            if cfg_screen:
                self._state("configuração do modo", seen)
                if now - self.last_mode < MODE_COOLDOWN_S:
                    return
                self.last_mode = now
                squad_on = find("mode_squad") is not None
                done = find("mode_done")
                confirm = False
                if squad_on:
                    if self.mode_cfg_taps > 0:
                        cfg.icon_dx = self.icon_dx          # acertou: lembra o ajuste para as próximas vezes
                        self.log("modo: Esquadrão selecionado (ajuste do clique %+.3f)" % self.icon_dx)
                    self.icon_start = None
                    confirm = True
                elif self.mode_cfg_taps < len(ICON_SWEEP):
                    if self.mode_cfg_taps == 0:
                        self.icon_start = self.icon_dx
                    self.icon_dx = max(-0.25, min(0.25, self.icon_start + ICON_SWEEP[self.mode_cfg_taps]))
                    self.mode_cfg_taps += 1
                    x, y = self._squad_icon(items, w, h, done)
                    x += self.icon_dx * h
                    self.log("modo: clicando em Esquadrão (toque %d, ajuste %+.3f, base: %s, x=%d y=%d, tela %dx%d) | lido: %s"
                             % (self.mode_cfg_taps, self.icon_dx, self._icon_src, x, y, w, h, seen))
                    self.io.click(x, y)
                else:
                    if self.icon_start is not None:         # não deu certo: desfaz o ajuste
                        self.icon_dx = self.icon_start
                        self.icon_start = None
                    confirm = True
                if confirm:
                    self.mode_cfg_taps = 0
                    if done is not None:
                        self.done_miss = 0
                        self.log("modo: confirmando (DONE)")
                        self.io.click(done.cx, done.cy)
                    else:
                        self.done_miss += 1
                        self.log("modo: não li o botão DONE (%d) | lido: %s" % (self.done_miss, seen))
                        if self.done_miss >= 2:
                            self.done_miss = 0
                            self.log("modo: fechando a configuração com ESC")
                            self.io.key(SC_ESC, 0.12)
                return
            self.mode_cfg_taps = 0
            if lobby_play is not None:
                panel = [it for it in items if it.cx < w * 0.30 and h * 0.30 < it.cy < h * 0.92]
                if match("mode_squad", panel) is not None:
                    self.mode_tries = 0
                else:
                    self._state("modo errado (não é Esquadrão)", seen)
                    if now - self.last_mode < MODE_COOLDOWN_S:
                        return
                    if self.mode_tries >= MODE_MAX_TRIES:
                        self.on_stop("não consegui colocar em Esquadrão | lido: %s" % seen)
                        return
                    self.last_mode = now
                    card = match("mode_card", panel, h * 0.50)
                    if card is not None and card.cy < h * 0.82:
                        self.mode_tries += 1
                        self.log("modo: abrindo a configuração (tentativa %d)" % self.mode_tries)
                        self.io.click(card.cx, card.cy)
                    else:
                        self.log("modo: não li o resumo do modo no lobby, não vou chutar posição | lido: %s" % seen)
                    return

        # 6) Lobby: puxa partida (ignora o JOGAR do menu do topo)
        play = find("play", h * 0.35)
        if play is not None:
            self._state("lobby", seen)
            if now - self.last_play < PLAY_COOLDOWN_S:
                return
            self.last_play = now
            self.matches += 1
            self.last_queue = now
            press(play, "iniciar partida #%d" % self.matches)
            return

        # 7) Em partida
        self._state("partida", seen)
        if cfg.afk and now - self.last_afk > AFK_S:
            self.last_afk = now
            self.log("anti-AFK")
            self.io.hold_key(SC_W, 0.7)

    def _treasure_name(self, items, w, h):
        """Nome do tesouro: o texto maior do lado direito da tela, mais o tipo que fica logo acima."""
        c = [it for it in items if it.cx > w * 0.55 and h * 0.12 < it.cy < h * 0.55 and len(it.text) > 2]
        if not c:
            return "tesouro"
        best = max(c, key=lambda it: it.y2 - it.y1)
        name = best.text.capitalize()
        bh = best.y2 - best.y1
        above = [it for it in c if it is not best and it.cy < best.cy and best.cy - it.cy < bh * 2.5]
        if above:
            name += " (%s)" % max(above, key=lambda it: it.cy).text.capitalize()
        return name

    def _squad_icon(self, items, w, h, done):
        """Acha o ícone de Esquadrão (4º ícone de Tamanho da Equipe)."""
        keys = self.cfg.keys_for("mode_size")
        cands = [it for it in items
                 if it.cx > w * 0.40 and h * 0.30 < it.cy < h * 0.65 and any(k in it.text for k in keys)]
        if cands:
            v = min(cands, key=lambda it: it.x1)       # valor atual (ex.: ESQUADRAO), alinhado à esquerda
            self._icon_src = "texto '%s' em x=%d" % (v.text[:12], v.x1)
            return v.x1 + 0.484 * h, v.cy - 0.028 * h
        if done is not None:
            self._icon_src = "botão DONE"
            return done.cx + 0.053 * h, done.cy - 0.50 * h
        self._icon_src = "posição fixa"
        return w * SQUAD_X, h * SQUAD_Y


# ----------------------------------------------------------------------------
# Windows: janela do jogo, mouse e teclado
# ----------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]

    def set_dpi_aware():
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    def _send(inp):
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _key_event(scan, up):
        flags = 0x0008 | (0x0002 if up else 0)          # KEYEVENTF_SCANCODE | KEYUP
        inp = INPUT(type=1)
        inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        _send(inp)

    def _mouse_event(flags, dx=0, dy=0):
        inp = INPUT(type=0)
        inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        _send(inp)

    def find_game_window():
        found = []

        def cb(hwnd, lparam):
            try:
                if user32.IsWindowVisible(hwnd):
                    n = user32.GetWindowTextLengthW(hwnd)
                    if n:
                        buf = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(hwnd, buf, n + 1)
                        if buf.value.strip() == GAME_TITLE:
                            found.append(hwnd)
            except Exception:
                pass
            return True

        user32.EnumWindows(_ENUMPROC(cb), 0)
        return found[0] if found else None

    def client_region(hwnd):
        """(left, top, width, height) da área do jogo na tela, ou None se minimizado."""
        if user32.IsIconic(hwnd):
            return None
        rc = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 200 or h < 200:
            return None
        return pt.x, pt.y, w, h

    def focus_window(hwnd):
        try:
            if user32.GetForegroundWindow() == hwnd:
                return
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)               # SW_RESTORE
            _key_event(0x38, False)                      # Alt: destrava o SetForegroundWindow
            _key_event(0x38, True)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
        except Exception:
            pass

    def hotkey_down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class WinIO:
    """Clique e teclado. As coordenadas chegam relativas à área capturada."""

    def __init__(self):
        self.origin = (0, 0)
        self.hwnd = None
        self.warn = None            # função de log (preenchida pelo Worker)
        self._warned = False

    def _focus(self):
        if self.hwnd:
            focus_window(self.hwnd)

    def click(self, x, y):
        self._focus()
        sx, sy = int(self.origin[0] + x), int(self.origin[1] + y)
        user32.SetCursorPos(sx, sy)
        time.sleep(0.10)
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            if (abs(pt.x - sx) > 2 or abs(pt.y - sy) > 2) and self.warn and not self._warned:
                self._warned = True
                self.warn("ATENÇÃO: pedi o mouse em (%d,%d) mas o Windows colocou em (%d,%d). "
                          "Problema de escala da tela: me mande esta linha." % (sx, sy, pt.x, pt.y))
        except Exception:
            pass
        _mouse_event(0x0001, 1, 0)       # pequeno movimento: o jogo registra o "hover"
        _mouse_event(0x0001, -1, 0)
        time.sleep(0.08)
        _mouse_event(0x0002)             # botão esquerdo para baixo
        time.sleep(0.07)
        _mouse_event(0x0004)             # botão esquerdo solto

    def key(self, scan, hold=0.06):
        self._focus()
        _key_event(scan, False)
        time.sleep(hold)
        _key_event(scan, True)

    def hold_key(self, scan, seconds):
        self.key(scan, seconds)


# ----------------------------------------------------------------------------
# OCR e captura
# ----------------------------------------------------------------------------
class Ocr:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR()

    def read(self, img):
        """img: array BGR. Retorna lista de (texto, x1, y1, x2, y2)."""
        try:
            res, _ = self.engine(img, use_cls=False)
        except TypeError:
            res, _ = self.engine(img)
        out = []
        for it in res or []:
            box, text = it[0], str(it[1])
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((text, min(xs), min(ys), max(xs), max(ys)))
        return out


class Capture:
    def __init__(self):
        import mss
        self.sct = mss.mss()

    def monitor_region(self, idx):
        mons = self.sct.monitors
        idx = idx if 1 <= idx < len(mons) else 1
        m = mons[idx]
        return m["left"], m["top"], m["width"], m["height"]

    def grab(self, region):
        import numpy as np
        left, top, width, height = region
        shot = self.sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
        arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(arr[:, :, :3])


# ----------------------------------------------------------------------------
# Trabalhador: captura -> OCR -> bot
# ----------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self, cfg, logq):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.logq = logq
        self.running = False
        self.stop_flag = False
        self.ready = False
        self.mask_rect = None          # (x, y, w, h) da janela deste app na tela
        self.debug_request = False
        self.io = WinIO()
        self.io.warn = self.log
        self.bot = Bot(cfg, self.io, self.log, on_stop=self._auto_stop)
        self.bot.notify = self.notify
        self.auto_stopped = False
        self.stop_reason = ""
        self._last_black = 0.0

    def log(self, msg):
        self.logq.put(time.strftime("%H:%M:%S") + "  " + msg)

    def notify(self, title, body, urgent=False):
        """Aviso de tesouro: Log e (se houver tópico) push no celular pelo ntfy.sh."""
        self.log("NOTIFICAÇÃO: %s - %s" % (title, body))
        topic = (self.cfg.ntfy or "").strip()
        if topic:
            threading.Thread(target=self._push, args=(topic, title, body, urgent), daemon=True).start()

    def _push(self, topic, title, body, urgent):
        try:
            import urllib.request
            import urllib.parse
            safe = lambda t: t.encode("ascii", "ignore").decode("ascii")   # cabeçalhos HTTP só aceitam ASCII
            req = urllib.request.Request(
                "https://ntfy.sh/" + urllib.parse.quote(topic, safe=""),
                data=body.encode("utf-8"),
                headers={"Title": safe(title), "Priority": "5" if urgent else "3",
                         "Tags": "pirate_flag" if urgent else "gift"},
                method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            self.log("aviso enviado ao celular (ntfy)")
        except Exception as e:
            self.log("falha ao enviar o aviso ao celular: %s" % e)

    def _auto_stop(self, why):
        self.stop_reason = why
        self.running = False
        self.auto_stopped = True
        self.log("PAUSADO: " + why)

    def start_farm(self):
        self.bot.reset(time.time())
        self.auto_stopped = False
        self.running = True
        self.log("INICIADO (sair por tempo: %s min)" % self.cfg.minutes)

    def stop_farm(self):
        if self.running:
            self.log("PAUSADO")
        self.running = False

    def _region(self, cap):
        hwnd = None
        if self.cfg.capture == "auto":
            hwnd = find_game_window()
        self.io.hwnd = hwnd
        if hwnd:
            reg = client_region(hwnd)
            if reg:
                return reg
        return cap.monitor_region(self.cfg.monitor)

    def run(self):
        import numpy as np
        try:
            self.log("Carregando o OCR (pode levar alguns segundos)...")
            ocr = Ocr()
            cap = Capture()
            self.ready = True
            self.log("Pronto. Clique em INICIAR ou pressione F6. Deixe o Fortnite em primeiro plano. F7 salva um debug.")
        except Exception:
            self.log("ERRO ao iniciar o OCR/captura:\n" + traceback.format_exc())
            return
        while not self.stop_flag:
            if not self.running:
                if self.debug_request:
                    try:
                        self._debug_only(ocr, cap, np)
                    except Exception:
                        self.debug_request = False
                        self.log("erro ao salvar o debug: " + traceback.format_exc().strip().splitlines()[-1])
                time.sleep(0.2)
                continue
            t0 = time.time()
            try:
                self._tick(ocr, cap, np)
            except Exception:
                self.log("erro no ciclo: " + traceback.format_exc().strip().splitlines()[-1])
            time.sleep(max(0.2, self.cfg.tick - (time.time() - t0)))

    def _tick(self, ocr, cap, np):
        region = self._region(cap)
        img = cap.grab(region)
        h, w = img.shape[:2]
        # Esconde este app (para o OCR não ler os próprios botões)
        mr = self.mask_rect
        if mr:
            x0 = max(0, int(mr[0] - region[0]))
            y0 = max(0, int(mr[1] - region[1]))
            x1 = min(w, int(mr[0] + mr[2] - region[0]))
            y1 = min(h, int(mr[1] + mr[3] - region[1]))
            if x1 > x0 and y1 > y0:
                img[y0:y1, x0:x1] = 0
        if img.mean() < 3 and time.time() - self._last_black > 60:
            self._last_black = time.time()
            self.log("A captura está preta. No Fortnite use o modo de janela 'Tela cheia em janela'.")
        scale = ocr_scale(w, self.cfg.ocr_width)
        small = img
        if abs(scale - 1.0) > 0.01:
            from PIL import Image
            small = np.array(Image.fromarray(img).resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                                         Image.LANCZOS if scale > 1.0 else Image.BILINEAR))
        raw = ocr.read(small)
        items = [Hit(norm(t), x1 / scale, y1 / scale, x2 / scale, y2 / scale) for (t, x1, y1, x2, y2) in raw]
        seen = " | ".join(t for (t, _a, _b, _c, _d) in raw)[:420]
        if self.debug_request:
            self.debug_request = False
            self._save_debug(img, raw, scale)
        self.io.origin = (region[0], region[1])
        try:
            self.bot.burger = find_hamburgers(img) if self.bot.leaving else []
        except Exception:
            self.bot.burger = []
        self.bot.handle(items, w, h, time.time(), seen)
        if self.bot.want_debug:
            self.bot.want_debug = False
            self._save_debug(img, raw, scale)

    def _debug_only(self, ocr, cap, np):
        """Salva print + texto lido mesmo com o farm pausado (atalho F7)."""
        self.debug_request = False
        region = self._region(cap)
        img = cap.grab(region)
        h, w = img.shape[:2]
        mr = self.mask_rect
        if mr:
            x0, y0 = max(0, int(mr[0] - region[0])), max(0, int(mr[1] - region[1]))
            x1, y1 = min(w, int(mr[0] + mr[2] - region[0])), min(h, int(mr[1] + mr[3] - region[1]))
            if x1 > x0 and y1 > y0:
                img[y0:y1, x0:x1] = 0
        scale = ocr_scale(w, self.cfg.ocr_width)
        small = img
        if abs(scale - 1.0) > 0.01:
            from PIL import Image
            small = np.array(Image.fromarray(img).resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                                         Image.LANCZOS if scale > 1.0 else Image.BILINEAR))
        self._save_debug(img, ocr.read(small), scale)

    def _save_debug(self, img, raw, scale):
        try:
            from PIL import Image
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = os.path.dirname(config_path())
            png = os.path.join(base, "debug_%s.png" % stamp)
            txt = os.path.join(base, "debug_%s.txt" % stamp)
            Image.fromarray(img[:, :, ::-1]).save(png)
            with open(txt, "w", encoding="utf-8") as f:
                f.write("tela %dx%d | escala OCR %.2f\n" % (img.shape[1], img.shape[0], scale))
                for (t, x1, y1, x2, y2) in raw:
                    f.write("%s  [%d,%d,%d,%d]\n" % (t, x1 / scale, y1 / scale, x2 / scale, y2 / scale))
                try:
                    f.write("3 barrinhas achadas: %s\n" % [(int(a), int(b)) for a, b, _c in find_hamburgers(img)])
                except Exception:
                    pass
            self.log("Debug salvo em: " + base)
        except Exception:
            self.log("falha ao salvar o debug: " + traceback.format_exc().strip().splitlines()[-1])


# ----------------------------------------------------------------------------
# Interface (Tkinter)
# ----------------------------------------------------------------------------
BG = "#0d0f12"
CARD = "#15181d"
BORDER = "#252a32"
TEXT = "#e8eaed"
MUTED = "#8a919c"
GREEN = "#1fa653"
GREEN_HOVER = "#27bd61"
RED = "#ff5a5a"
RED_BTN = "#d94343"
YELLOW = "#f2e413"
OFF = "#3a3f48"


def run_gui():
    import tkinter as tk

    if IS_WIN:
        set_dpi_aware()

    cfg = Settings()
    cfg.load()
    logq = queue.Queue()
    worker = Worker(cfg, logq)

    root = tk.Tk()
    root.title("Fortnite - Auto Jogar Novamente")
    root.configure(bg=BG)
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        scale = 1.0

    def S(n):
        return int(round(n * scale))

    root.geometry("%dx%d" % (S(660), S(900)))
    root.minsize(S(560), S(640))

    FONT = "Segoe UI"

    class Toggle(tk.Canvas):
        def __init__(self, master, var, bg=CARD, command=None):
            super().__init__(master, width=S(46), height=S(24), bg=bg, highlightthickness=0, cursor="hand2")
            self.var = var
            self.command = command
            self.bind("<Button-1>", self._flip)
            var.trace_add("write", lambda *a: self._draw())
            self._draw()

        def _flip(self, _e=None):
            self.var.set(not self.var.get())
            if self.command:
                self.command()

        def _draw(self):
            self.delete("all")
            on = self.var.get()
            w, h = S(46), S(24)
            col = GREEN if on else OFF
            self.create_oval(0, 0, h, h, fill=col, outline=col)
            self.create_oval(w - h, 0, w, h, fill=col, outline=col)
            self.create_rectangle(h / 2, 0, w - h / 2, h, fill=col, outline=col)
            d = h - S(6)
            x = (w - h + S(3)) if on else S(3)
            self.create_oval(x, S(3), x + d, S(3) + d, fill="white", outline="white")

    def card(parent, pady=(0, 10)):
        f = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        f.pack(fill="x", padx=S(16), pady=pady)
        return f

    # ---- Cabeçalho
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=S(16), pady=(S(14), S(8)))
    tk.Label(head, text="AUTO FARM", bg=BG, fg="white", font=(FONT, 15, "bold")).pack(side="left")
    tk.Label(head, text="  FORTNITE", bg=BG, fg=YELLOW, font=(FONT, 15, "bold")).pack(side="left")
    hk = tk.Label(head, text=" F6 ", bg="#1d2127", fg=TEXT, font=(FONT, 9, "bold"))
    hk.pack(side="right")
    tk.Label(head, text="atalho ", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="right")

    # ---- Status
    st = card(root)
    top = tk.Frame(st, bg=CARD)
    top.pack(fill="x", padx=S(14), pady=(S(12), 0))
    dot = tk.Canvas(top, width=S(12), height=S(12), bg=CARD, highlightthickness=0)
    dot.pack(side="left", pady=(S(4), 0))
    dot_id = dot.create_oval(1, 1, S(11), S(11), fill=RED, outline=RED)
    status_lbl = tk.Label(top, text="  CARREGANDO", bg=CARD, fg=MUTED, font=(FONT, 13, "bold"))
    status_lbl.pack(side="left")
    sub_lbl = tk.Label(st, text="Carregando o OCR...", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
    sub_lbl.pack(fill="x", padx=S(14), pady=(0, S(8)))

    stats = tk.Frame(st, bg=CARD)
    stats.pack(fill="x", padx=S(14), pady=(0, S(14)))
    stats.columnconfigure(0, weight=1, uniform="a")
    stats.columnconfigure(1, weight=1, uniform="a")

    def stat_box(col, title):
        b = tk.Frame(stats, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        b.grid(row=0, column=col, sticky="ew", padx=(0, S(8)) if col == 0 else (0, 0))
        tk.Label(b, text=title, bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", padx=S(10), pady=(S(8), 0))
        v = tk.Label(b, text="0", bg=BG, fg=YELLOW, font=(FONT, 18, "bold"))
        v.pack(anchor="w", padx=S(10), pady=(0, S(8)))
        return v

    matches_lbl = stat_box(0, "PARTIDAS")
    next_lbl = stat_box(1, "PRÓXIMA SAÍDA")
    next_lbl.config(text="—", fg=MUTED)

    treasure_lbl = tk.Label(st, text="Tesouros resgatados: 0", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
    treasure_lbl.pack(fill="x", padx=S(14), pady=(0, S(12)))

    # ---- Botão iniciar/parar
    state = {"adv": False}

    def set_ui_running(running):
        if running:
            start_btn.config(text="■   PARAR", bg=RED_BTN, activebackground="#ef5454")
            dot.itemconfig(dot_id, fill=GREEN, outline=GREEN)
            status_lbl.config(text="  RODANDO", fg=GREEN)
            sub_lbl.config(text="Deixe o Fortnite em primeiro plano. F6 pausa.", fg=MUTED)
        else:
            start_btn.config(text="▶   INICIAR", bg=GREEN, activebackground=GREEN_HOVER)
            dot.itemconfig(dot_id, fill=RED, outline=RED)
            status_lbl.config(text="  PAUSADO", fg=RED)
            sub_lbl.config(text="Clique em INICIAR (no lobby ou já na partida)", fg=MUTED)

    def toggle():
        if not worker.ready:
            return
        if worker.running:
            worker.stop_farm()
            set_ui_running(False)
            try:
                root.deiconify()
            except Exception:
                pass
        else:
            apply_settings()
            worker.start_farm()
            set_ui_running(True)
            if cfg.minimize:
                try:
                    root.iconify()
                except Exception:
                    pass

    start_btn = tk.Button(root, text="▶   INICIAR", bg=GREEN, fg="white", activebackground=GREEN_HOVER,
                          activeforeground="white", font=(FONT, 13, "bold"), relief="flat", bd=0,
                          cursor="hand2", command=toggle)
    start_btn.pack(fill="x", padx=S(16), pady=(0, S(10)), ipady=S(10))

    # ---- Automação
    auto = card(root)
    tk.Label(auto, text="AUTOMAÇÃO", bg=CARD, fg=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(
        fill="x", padx=S(14), pady=(S(10), S(2)))

    v_dead = tk.BooleanVar(value=cfg.dead)
    v_time = tk.BooleanVar(value=cfg.by_time)
    v_afk = tk.BooleanVar(value=cfg.afk)
    v_cont = tk.BooleanVar(value=cfg.cont)
    v_mode = tk.BooleanVar(value=cfg.mode)
    v_min = tk.BooleanVar(value=cfg.minimize)
    v_minutes = tk.StringVar(value=("%g" % cfg.minutes))

    def row(parent, var, title, subtitle, extra=None):
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", padx=S(14), pady=S(6))
        Toggle(r, var).pack(side="right")
        t = tk.Frame(r, bg=CARD)
        t.pack(side="left", fill="x", expand=True)
        tk.Label(t, text=title, bg=CARD, fg=TEXT, font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
        if extra is None:
            tk.Label(t, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        else:
            line = tk.Frame(t, bg=CARD)
            line.pack(fill="x")
            tk.Label(line, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
            extra(line)

    row(auto, v_dead, "Sair quando o esquadrão morrer", "Volta ao lobby e puxa outra partida")

    def minutes_widget(line):
        sp = tk.Spinbox(line, from_=1, to=120, increment=0.5, width=5, textvariable=v_minutes,
                        bg=BG, fg=TEXT, buttonbackground=CARD, insertbackground=TEXT, relief="flat",
                        highlightbackground=BORDER, highlightthickness=1, font=(FONT, 9))
        sp.pack(side="left", padx=S(6))
        tk.Label(line, text="min", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")

    row(auto, v_time, "Sair e puxar por tempo", "A cada", minutes_widget)
    row(auto, v_afk, "Anti-AFK", "Aperta W de vez em quando durante a partida")
    row(auto, v_cont, "Clicar em CONTINUAR", "Na tela de eliminação")
    row(auto, v_mode, "Garantir Esquadrão", "Troca para Esquadrão se o lobby voltar em Solo/Duplas")
    row(auto, v_min, "Minimizar ao iniciar", "Esconde esta janela (use F6 para pausar)")
    tk.Frame(auto, bg=CARD, height=S(6)).pack()

    # ---- Avançado
    adv_head = tk.Label(root, text="⚙  Configurações avançadas  ›", bg=BG, fg=MUTED, font=(FONT, 9),
                        cursor="hand2", anchor="w")
    adv_head.pack(fill="x", padx=S(20), pady=(0, S(6)))
    adv = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    key_vars = {}
    num_vars = {}

    def build_adv():
        pad = dict(padx=S(12), pady=S(3))
        if SHOW_TEXT_EDITOR:
            tk.Label(adv, text="Textos que o app procura na tela (separe por vírgula, sem se preocupar com acento)",
                     bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w", wraplength=S(560), justify="left").pack(
                fill="x", padx=S(12), pady=(S(10), S(2)))
            for name, label in KEY_LABELS.items():
                f = tk.Frame(adv, bg=CARD)
                f.pack(fill="x", **pad)
                tk.Label(f, text=label, bg=CARD, fg=TEXT, font=(FONT, 9), anchor="w").pack(fill="x")
                var = tk.StringVar(value=cfg.keys.get(name) or DEFAULT_KEYS[name])
                key_vars[name] = var
                tk.Entry(f, textvariable=var, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat",
                         highlightbackground=BORDER, highlightthickness=1, font=("Consolas", 9)).pack(fill="x")
        nums = tk.Frame(adv, bg=CARD)
        nums.pack(fill="x", padx=S(12), pady=(S(8), S(4)))
        for i, (name, label, val) in enumerate((
                ("tick", "Segundos entre leituras", cfg.tick),
                ("ocr_width", "Largura do OCR (px)", cfg.ocr_width),
                ("monitor", "Monitor (tela inteira)", cfg.monitor))):
            tk.Label(nums, text=label, bg=CARD, fg=TEXT, font=(FONT, 9)).grid(row=0, column=i * 2, sticky="w",
                                                                              padx=(0, S(4)))
            var = tk.StringVar(value=("%g" % val))
            num_vars[name] = var
            tk.Entry(nums, textvariable=var, width=6, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightbackground=BORDER, highlightthickness=1).grid(row=0, column=i * 2 + 1,
                                                                            padx=(0, S(12)))
        v_cap = tk.BooleanVar(value=(cfg.capture == "auto"))
        num_vars["cap"] = v_cap
        r = tk.Frame(adv, bg=CARD)
        r.pack(fill="x", padx=S(12), pady=S(4))
        Toggle(r, v_cap).pack(side="right")
        tk.Label(r, text="Capturar só a janela do Fortnite (desligado = tela inteira)", bg=CARD, fg=TEXT,
                 font=(FONT, 9)).pack(side="left")
        r2 = tk.Frame(adv, bg=CARD)
        r2.pack(fill="x", padx=S(12), pady=S(4))
        tk.Label(r2, text="Aviso no celular (tópico do ntfy)", bg=CARD, fg=TEXT, font=(FONT, 9)).pack(side="left")
        v_ntfy = tk.StringVar(value=cfg.ntfy)
        num_vars["ntfy"] = v_ntfy
        tk.Entry(r2, textvariable=v_ntfy, width=26, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat",
                 highlightbackground=BORDER, highlightthickness=1).pack(side="right")
        btns = tk.Frame(adv, bg=CARD)
        btns.pack(fill="x", padx=S(12), pady=(S(6), S(12)))

        def restore():
            for n, v in key_vars.items():
                v.set(DEFAULT_KEYS[n])
            log_line("Textos restaurados para o padrão.")

        def debug():
            if not worker.ready:
                return
            worker.debug_request = True

        if SHOW_TEXT_EDITOR:
            tk.Button(btns, text="Restaurar textos", command=restore, bg="#232830", fg=TEXT, relief="flat",
                      activebackground="#2d333d", activeforeground=TEXT, font=(FONT, 9), cursor="hand2").pack(
                side="left", ipadx=S(8), ipady=S(3))
        def test_notify():
            apply_settings()
            worker.notify("Teste", "Se voce viu isto, o aviso esta funcionando.", False)

        tk.Button(btns, text="Testar aviso", command=test_notify, bg="#232830", fg=TEXT, relief="flat",
                  activebackground="#2d333d", activeforeground=TEXT, font=(FONT, 9), cursor="hand2").pack(
            side="left", ipadx=S(8), ipady=S(3))
        tk.Button(btns, text="Salvar debug (print + texto lido)", command=debug, bg="#232830", fg=TEXT,
                  relief="flat", activebackground="#2d333d", activeforeground=TEXT, font=(FONT, 9),
                  cursor="hand2").pack(side="left", padx=S(8), ipadx=S(8), ipady=S(3))

    def toggle_adv(_e=None):
        if state["adv"]:
            adv.pack_forget()
            adv_head.config(text="⚙  Configurações avançadas  ›")
            state["adv"] = False
        else:
            if not num_vars:
                build_adv()
            adv.pack(fill="x", padx=S(16), pady=(0, S(10)), before=log_card)
            adv_head.config(text="⚙  Configurações avançadas  ⌄")
            state["adv"] = True

    adv_head.bind("<Button-1>", toggle_adv)

    # ---- Atividade
    log_card = tk.Frame(root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    log_card.pack(fill="both", expand=True, padx=S(16), pady=(0, S(14)))
    tk.Label(log_card, text="ATIVIDADE", bg=CARD, fg=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(
        fill="x", padx=S(14), pady=(S(10), S(4)))
    log_box = tk.Text(log_card, height=8, bg=BG, fg="#cfd3da", font=("Consolas", 9), relief="flat",
                      wrap="word", state="disabled", highlightbackground=BORDER, highlightthickness=1)
    log_box.pack(fill="both", expand=True, padx=S(14), pady=(0, S(12)))

    def log_line(msg):
        log_box.config(state="normal")
        log_box.insert("end", msg + "\n")
        n = int(log_box.index("end-1c").split(".")[0])
        if n > 400:
            log_box.delete("1.0", "%d.0" % (n - 400))
        log_box.see("end")
        log_box.config(state="disabled")

    # ---- Aplicar/salvar configurações
    def apply_settings():
        cfg.dead = bool(v_dead.get())
        cfg.by_time = bool(v_time.get())
        cfg.afk = bool(v_afk.get())
        cfg.cont = bool(v_cont.get())
        cfg.mode = bool(v_mode.get())
        cfg.minimize = bool(v_min.get())
        try:
            cfg.minutes = max(0.5, float(v_minutes.get().replace(",", ".")))
        except Exception:
            pass
        if num_vars:
            for n, v in key_vars.items():
                txt = v.get().strip()
                if txt and txt != DEFAULT_KEYS[n]:
                    cfg.keys[n] = txt
                else:
                    cfg.keys.pop(n, None)
            for n, cast in (("tick", float), ("ocr_width", int), ("monitor", int)):
                try:
                    val = cast(float(num_vars[n].get().replace(",", ".")))
                    if n == "tick":
                        val = max(0.5, val)
                    if n == "ocr_width":
                        val = max(480, val)
                    setattr(cfg, n, val)
                except Exception:
                    pass
            cfg.capture = "auto" if num_vars["cap"].get() else "monitor"
            if "ntfy" in num_vars:
                cfg.ntfy = num_vars["ntfy"].get().strip()
        cfg.save()

    # ---- Atualização periódica
    prev = {"f6": False, "ready": False, "auto_stop": False}

    def pump():
        try:
            while True:
                log_line(logq.get_nowait())
        except queue.Empty:
            pass
        if worker.ready and not prev["ready"]:
            prev["ready"] = True
            set_ui_running(False)
        if worker.auto_stopped and not prev["auto_stop"]:
            prev["auto_stop"] = True
            set_ui_running(False)
            sub_lbl.config(text=worker.stop_reason or "Pausado", fg=YELLOW)
            try:
                root.deiconify()
            except Exception:
                pass
        if not worker.auto_stopped:
            prev["auto_stop"] = False
        matches_lbl.config(text=str(worker.bot.matches))
        tb = worker.bot
        treasure_lbl.config(
            text="Tesouros resgatados: %d%s" % (tb.treasures, ("  ·  último: " + tb.last_treasure) if tb.last_treasure else ""),
            fg=YELLOW if tb.treasures else MUTED)
        left = worker.bot.seconds_to_exit(time.time()) if worker.running else None
        if left is None:
            next_lbl.config(text="—", fg=MUTED)
        else:
            next_lbl.config(text="%d:%02d" % (left // 60, left % 60), fg=YELLOW)
        # F6 global
        if IS_WIN:
            down = hotkey_down(0x75)
            if down and not prev["f6"]:
                toggle()
            prev["f6"] = down
            down7 = hotkey_down(0x76)
            if down7 and not prev.get("f7") and worker.ready:
                worker.debug_request = True
                log_line("F7: salvando print + texto lido...")
            prev["f7"] = down7
        # área deste app (o OCR ignora essa região)
        try:
            if root.state() == "normal":
                worker.mask_rect = (root.winfo_rootx(), root.winfo_rooty() - S(45),
                                    root.winfo_width(), root.winfo_height() + S(45))
            else:
                worker.mask_rect = None
        except Exception:
            worker.mask_rect = None
        root.after(150, pump)

    def on_close():
        try:
            apply_settings()
        except Exception:
            pass
        worker.running = False
        worker.stop_flag = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    worker.start()
    pump()
    root.mainloop()


# ----------------------------------------------------------------------------
# Auto-teste (usado pelo GitHub para conferir se o OCR empacotado funciona)
# ----------------------------------------------------------------------------
def selftest():
    lines = []
    ok = True
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        import tkinter  # noqa: F401
        import mss  # noqa: F401
        img = Image.new("RGB", (1000, 220), (30, 30, 120))
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arialbd.ttf", 80)
        except Exception:
            font = ImageFont.load_default()
        d.text((30, 60), "RETURN TO LOBBY", fill=(255, 255, 255), font=font)
        arr = np.array(img)[:, :, ::-1].copy()
        hits = Ocr().read(arr)
        txt = " | ".join(x[0] for x in hits)
        lines.append("OCR leu: " + txt)
        if "LOBBY" not in txt.upper():
            ok = False
            lines.append("FALHOU: o OCR nao achou LOBBY")
        else:
            lines.append("OK")
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    try:
        with open(os.path.join(app_dir(), "selftest.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    return 0 if ok else 1


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if not IS_WIN:
        print("Este app é para Windows.")
        sys.exit(1)
    run_gui()


if __name__ == "__main__":
    main()
