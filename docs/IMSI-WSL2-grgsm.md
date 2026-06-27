# GSM / IMSI com gr-gsm no WSL2 (Windows + HackRF)

> ⚠️ **USO LEGAL E AUTORIZADO APENAS.**
> Decodificar GSM captura identidades de assinantes (IMSI/TMSI) e metadados de celular.
> Só faça isso com **o seu próprio SIM**, em **laboratório**, ou com **autorização escrita**
> (pentest/pesquisa). Capturar tráfego/identidades de terceiros é **interceptação ilegal**
> na maioria dos países (no Brasil, viola a Lei de Interceptação e o Marco Civil).
> Esta documentação é para **pesquisa de segurança defensiva e educacional**.

O `gr-gsm` (GNU Radio + osmocom) **não roda no Windows**. O caminho prático é rodá-lo
dentro do **WSL2 (Linux sob Windows)** e passar o HackRF para o WSL via `usbipd-win`.

---

## Visão geral

```
HackRF (USB)
   │  usbipd-win (passa o USB p/ o Linux)
   ▼
WSL2 (Ubuntu)
   ├── hackrf tools        (hackrf_info confirma o device)
   ├── gr-gsm              (grgsm_livemon_headless decodifica GSM)
   └── tshark/pyshark      (lê o GSMTAP e extrai IMSI/TMSI)
```

---

## 1. Instalar o WSL2 (PowerShell como Admin)

```powershell
wsl --install -d Ubuntu
# reinicie o Windows quando pedir; crie usuário/senha do Ubuntu no 1º boot
wsl --set-default-version 2
wsl --update
```

## 2. Passar o HackRF para o WSL (usbipd-win)

```powershell
winget install --id dorssel.usbipd-win
# liste os dispositivos USB e ache o HackRF (VID 1d50)
usbipd list
# vincule e anexe ao WSL (troque 1-4 pelo BUSID do HackRF)
usbipd bind   --busid 1-4
usbipd attach --wsl --busid 1-4
```

No Ubuntu (WSL), confirme:

```bash
lsusb            # deve listar "OpenMoko / Great Scott Gadgets HackRF One"
```

> Toda vez que reconectar o HackRF, repita o `usbipd attach`. Em WSL recente, dá para
> usar `wsl --update` + networking espelhado para simplificar.

## 3. Instalar hackrf + gr-gsm no Ubuntu

Rode o script incluído (`scripts/setup-grgsm-wsl.sh`) **dentro do WSL**:

```bash
cd /mnt/c/Dev/hackrf/sinalrf
chmod +x scripts/setup-grgsm-wsl.sh
./scripts/setup-grgsm-wsl.sh
```

Ele instala `hackrf`, `gnuradio`, `gr-osmosdr` e compila o fork mantido do
**`gr-gsm` (velichkov/gr-gsm)**, compatível com GNU Radio 3.10 do Ubuntu 22.04/24.04.
Confirme:

```bash
hackrf_info                 # acha o HackRF (via usbipd)
grgsm_livemon_headless --help
```

## 4. Capturar (monitor ao vivo)

```bash
# sintoniza uma portadora GSM ativa (ex.: 935.2 MHz downlink Vivo) e envia GSMTAP p/ 4729
grgsm_livemon_headless -f 935.2M -g 40 &

# lê o GSMTAP e mostra IMSI/TMSI/LAC/CID
sudo tshark -i lo -f 'udp port 4729' -Y 'gsm_a.imsi || gsm_a.tmsi' \
  -T fields -e gsm_a.imsi -e gsm_a.tmsi -e gsm_a.lac
```

Para achar as portadoras GSM ativas antes:

```bash
sudo apt install -y kalibrate-hackrf
kal -s GSM900 -g 40      # lista canais; use a freq do mais forte no grgsm_livemon
```

---

## Integração com o mtzRF

A página **IMSI/INTCP** do mtzRF foi feita para macOS/Linux (onde o `server.py` roda o
gr-gsm localmente e escuta GSMTAP na UDP `4729`). No Windows, o `server.py` **não** roda o
gr-gsm — então o fluxo acima roda **dentro do WSL2, de forma independente** (grgsm_livemon
+ tshark). 

Para "ligar" os dois seria preciso encaminhar o GSMTAP do WSL2 para o servidor Windows
(porta 4729) — possível com networking espelhado do WSL ou um relay UDP, mas é um passo
extra e fora do escopo deste guia. Para pesquisa, o monitor no WSL2 já entrega os dados.

---

## Alternativas ao gr-gsm

- **Docker** (mais reprodutível): imagens com gr-gsm prontas evitam compilar.
- **kalibrate-hackrf**: só *descobre* células GSM e offset — não decodifica IMSI.
- **Raspberry Pi / caixa Linux dedicada**: roda gr-gsm nativo, sem WSL.
- **srsRAN**: stack LTE/5G completo (pesquisa de redes modernas; bem mais complexo).

> Lembrete: o **2G está sendo desligado** em muitas regiões, e em **LTE/5G** a captura de
> IMSI é muito mais difícil (identidades temporárias/cifradas). O gr-gsm é focado em 2G.
