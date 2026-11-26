"""Fuse boxes of the cars EVbro knows (Más → Coches → «Fusibles»): which fuse is which, its rating and what it feeds.

Ebro's owner's manuals do not print this: they only say where the two boxes are and send the owner to the diagram inside each
box cover. The table here comes from the workshop wiring manual of the car the Ebro S900 PHEV is built on (Chery's T28 PHEV
platform, sold in China as Fengyun T9; the Ebro manual carries the same platform code and the same engine, H4J15).

**It has not been checked against a real Ebro** (but for six positions of the cabin box that an owner found different on his
S900 and reported, noted on each), and it is the Chinese-market car: equipment differs between markets. Every
answer says so (`verified: False`), and the page tells the owner to trust the cover of the box over this table. Where that manual's
own table and drawing disagree, or a real car was seen to differ, the position carries a `doubt`. Only facts are kept (position, rating, function): no drawings.

Texts are in the three languages of the interface, here and not in the page's dictionaries, because they are data, not interface.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.vehicle_profiles import normalize_model

LANGS = ("es", "en", "pt")
Text = Tuple[str, str, str]                      # es, en, pt

SPARE: Text = ("Reserva (sin uso)", "Spare (unused)", "Reserva (sem uso)")
UNKNOWN: Text = ("El manual no dice para qué es", "The manual does not say what it is for", "O manual não diz para que serve")


def _bcm(n: int) -> Text:
    return (f"Módulo de carrocería (BCM), alimentación {n}", f"Body control module (BCM), supply {n}", f"Módulo de carroçaria (BCM), alimentação {n}")


# (position, amperes or None when the position is empty, function or None for a spare, doubt or None)
Row = Tuple[str, Optional[float], Optional[Text], Optional[Text]]

# What the owner of a real Ebro S900 PHEV found in his cabin box in November 2026, where it is not what the manual says (all of
# them positions the manual gives as spare or empty). One car: another version may differ, and the note says so.
UNCONFIRMED: Text = ("Función sin confirmar", "Function not confirmed", "Função por confirmar")
_ONE_CAR: Text = (" Es un solo coche: puede cambiar según la versión.", " That is one car: it may differ between versions.", " É um só carro: pode mudar conforme a versão.")


def _seen_without(amps: int) -> Text:
    """The manual fits an unused fuse here; the real car has none."""
    return (f"El manual pone aquí un fusible de {amps} A sin uso; en un Ebro S900 real (noviembre de 2026) no hay ninguno.{_ONE_CAR[0]}",
            f"The manual fits an unused {amps} A fuse here; a real Ebro S900 (November 2026) has none.{_ONE_CAR[1]}",
            f"O manual põe aqui um fusível de {amps} A sem uso; num Ebro S900 real (novembro de 2026) não há nenhum.{_ONE_CAR[2]}")


def _seen_other(amps: Optional[int], real: int) -> Text:
    """The real car carries a fuse of another rating than the manual's spare one (or a fuse where the manual has none)."""
    es = f"da esta posición como reserva de {amps} A" if amps else "deja esta posición vacía"
    en = f"gives this position as a {amps} A spare" if amps else "leaves this position empty"
    pt = f"dá esta posição como reserva de {amps} A" if amps else "deixa esta posição vazia"
    return (f"El manual {es}; en un Ebro S900 real (noviembre de 2026) lleva un fusible de {real} A, así que puede alimentar algo.{_ONE_CAR[0]}",
            f"The manual {en}; a real Ebro S900 (November 2026) carries a {real} A fuse, so it may feed something.{_ONE_CAR[1]}",
            f"O manual {pt}; num Ebro S900 real (novembro de 2026) tem um fusível de {real} A, por isso pode alimentar algo.{_ONE_CAR[2]}")


_CABIN: List[Row] = [
    ("RSB01", 20, ("Portón trasero eléctrico", "Power tailgate", "Porta da bagageira elétrica"), None),
    ("RSB02", 30, ("Ajuste del asiento del conductor", "Driver seat adjustment", "Regulação do banco do condutor"), None),
    ("RSB03", 30, ("Ajuste de los asientos de 2.ª o 3.ª fila, lado izquierdo", "2nd or 3rd row seat adjustment, left side", "Regulação dos bancos da 2.ª ou 3.ª fila, lado esquerdo"), None),
    ("RSB04", 30, None, None), ("RSB05", 30, None, None), ("RSB06", 30, None, None),
    ("RSB07", 30, ("Ajuste de los asientos de 2.ª o 3.ª fila, lado derecho", "2nd or 3rd row seat adjustment, right side", "Regulação dos bancos da 2.ª ou 3.ª fila, lado direito"), None),
    ("RSB08", 30, ("Control de las puertas delanteras", "Front door control", "Controlo das portas dianteiras"), None),
    ("RSB09", 30, ("Control de las puertas traseras", "Rear door control", "Controlo das portas traseiras"), None),
    ("RSB10", 30, ("Pretensor eléctrico del cinturón", "Electric seat belt pretensioner", "Pré-tensor elétrico do cinto"), None),
    ("RSB11", None, None, _seen_without(30)),
    ("RF01", 25, ("Ajuste del asiento del acompañante", "Front passenger seat adjustment", "Regulação do banco do passageiro"), None),
    ("RF02", 20, _bcm(3), None),
    ("RF03", None, None, None),
    ("RF04", 20, ("Asiento del acompañante (banqueta y respaldo) y calefacción del asiento derecho de la 2.ª fila",
                  "Front passenger seat (cushion and backrest) and heating of the right second-row seat",
                  "Banco do passageiro (assento e encosto) e aquecimento do banco direito da 2.ª fila"), None),
    ("RF05", 15, None, None), ("RF06", 15, None, None), ("RF07", 10, UNCONFIRMED, _seen_other(20, 10)),
    ("RF08", 20, ("Suspensión automática", "Automatic suspension", "Suspensão automática"), None),
    ("RF09", 15, None, None),
    ("RF10", 25, _bcm(6), None),
    ("RF11", 7.5, ("Pasarela central y pasarela Ethernet (comunicación entre centralitas)", "Central gateway and Ethernet gateway (communication between control units)",
                   "Gateway central e gateway Ethernet (comunicação entre unidades de controlo)"), None),
    ("RF12", 7.5, ("Contacto espiral del volante, toma de diagnosis (OBD) y relé del ventilador", "Steering wheel clock spring, diagnostic socket (OBD) and blower relay",
                   "Mola de relógio do volante, tomada de diagnóstico (OBD) e relé do ventilador"), None),
    ("RF13", 15, ("Carga inalámbrica del móvil", "Wireless phone charger", "Carregador sem fios do telemóvel"), None),
    ("RF14", 20, ("Centralita del cambio (TCU)", "Transmission control unit (TCU)", "Unidade de controlo da transmissão (TCU)"), None),
    ("RF15", 25, ("Amplificador de sonido", "Audio amplifier", "Amplificador de som"), None),
    ("RF16", 15, None, ("La tabla del manual deja esta posición vacía; los dibujos de la caja le ponen un fusible de 15 A sin uso.",
                        "The manual's table leaves this position empty; the drawings of the box show an unused 15 A fuse there.",
                        "A tabela do manual deixa esta posição vazia; os desenhos da caixa mostram aí um fusível de 15 A sem uso.")),
    ("RF17", None, None, None),
    ("RF18", 15, ("Piloto trasero izquierdo", "Left tail light", "Farolim traseiro esquerdo"), None),
    ("RF19", 30, ("Luneta térmica", "Heated rear window", "Vidro traseiro aquecido"), None),
    ("RF20", 15, ("Limpiaparabrisas trasero", "Rear wiper", "Limpa-vidros traseiro"), None),
    ("RF21", 25, None, None),
    ("RF22", 15, ("Piloto trasero derecho", "Right tail light", "Farolim traseiro direito"), None),
    ("RF23", None, None, None), ("RF24", None, None, None), ("RF25", None, None, None),
    ("RF26", 7.5, ("Pantalla y sonido, módulo de carrocería (BCM) y pasarela", "Head unit, body control module (BCM) and gateway", "Unidade de áudio, módulo de carroçaria (BCM) e gateway"), None),
    ("RF27", 7.5, ("ESP (control de estabilidad)", "ESP (stability control)", "ESP (controlo de estabilidade)"), None),
    ("RF28", 10, None, None),
    ("RF29", 10, ("Módulo de conexión remota (T-BOX), climatizador, panel de clima trasero, proyector HUD, cuadro de instrumentos y asiento del conductor",
                  "Telematics module (T-BOX), climate control, rear climate panel, head-up display, instrument cluster and driver seat",
                  "Módulo telemático (T-BOX), climatização, painel de clima traseiro, head-up display, painel de instrumentos e banco do condutor"), None),
    ("RF30", None, None, _seen_without(15)),
    ("RF31", 10, ("Tomas USB traseras", "Rear USB ports", "Tomadas USB traseiras"), None),
    ("RF32", 10, ("Airbags", "Airbags", "Airbags"), None),
    ("RF33", 10, ("Tomas USB delanteras y centralita del cambio (TCU)", "Front USB ports and transmission control unit (TCU)", "Tomadas USB dianteiras e unidade de controlo da transmissão (TCU)"), None),
    ("RF34", 10, ("Pantalla y sonido, módulo de carrocería (BCM) y carga inalámbrica del móvil", "Head unit, body control module (BCM) and wireless phone charger",
                  "Unidade de áudio, módulo de carroçaria (BCM) e carregador sem fios do telemóvel"), None),
    ("RF35", 15, ("Alimentación de reserva delantera", "Front spare power supply", "Alimentação de reserva dianteira"), None),
    ("RF36", 10, UNCONFIRMED, _seen_other(15, 10)),
    ("RF37", 20, ("Pantalla y sonido (unidad principal)", "Head unit", "Unidade de áudio principal"), None),
    ("RF38", 10, ("Pantallas del cuadro y central, T-BOX, módulos Bluetooth de las puertas, climatizador y sus trampillas, panel de clima trasero, HUD y mandos del volante",
                  "Instrument and centre displays, T-BOX, door Bluetooth modules, climate control and its flaps, rear climate panel, HUD and stalk switches",
                  "Ecrãs do painel e central, T-BOX, módulos Bluetooth das portas, climatização e as suas comportas, painel de clima traseiro, HUD e comandos do volante"), None),
    ("RF39", 10, ("Luz ambiental de las puertas, telepeaje (ETC), cámara de a bordo y sensor de lluvia y luz", "Door ambient lighting, toll tag (ETC), dash camera and rain and light sensor",
                  "Luz ambiente das portas, portagem eletrónica (ETC), câmara de bordo e sensor de chuva e luz"), None),
    ("RF40", 10, ("Módulo de carga USB trasero", "Rear USB charging module", "Módulo de carregamento USB traseiro"), None), ("RF41", None, None, None),
    ("RF42", 40, ("Ventilador del habitáculo", "Cabin blower", "Ventilador do habitáculo"), None),
    ("RF43", None, None, _seen_without(15)), ("RF44", 15, None, None), ("RF45", 15, None, None),
    ("RF46", 10, ("Calefacción de los retrovisores y señal del desempañado", "Mirror heating and demist feedback", "Aquecimento dos retrovisores e sinal de desembaciamento"), None),
    ("RF47", 15, ("Bomba del lavaparabrisas delantero y trasero", "Front and rear washer pump", "Bomba do lava-vidros dianteiro e traseiro"), None),
]
_CABIN_RELAYS: List[Tuple[str, Text]] = [("RRLY01", UNKNOWN), ("RRLY02", UNKNOWN), ("RRLY03", UNKNOWN)]

# Where each fuse sits in the cabin box, seen from the front (the side with the Chery logo and «9AR» moulded in it): rectangles
# (x1, y1, x2, y2) in the units of the drawing of that part. The drawing of the T28 PHEV manual and the one of the same part
# (9AR) on another car show the same fuses with the same ratings; the positions are read from the clearer one. RF03 and RF23-25
# have no slot; the three relays are not on this face.
_CABIN_FRAME = (40, 90, 1140, 1010)               # the part of the drawing that is the box
_CABIN_LAYOUT: Dict[str, Tuple[int, int, int, int]] = {
    "RF20": (200, 150, 234, 228), "RF02": (256, 150, 290, 228), "RSB06": (318, 137, 396, 235), "RSB03": (404, 137, 482, 235),
    "RSB10": (490, 137, 568, 235), "RF42": (761, 103, 839, 201), "RF18": (549, 255, 583, 333),
    "RF27": (872, 210, 956, 246), "RF30": (872, 265, 956, 301), "RF29": (872, 321, 956, 357), "RF28": (872, 375, 956, 411),
    "RF43": (872, 430, 956, 466), "RF31": (872, 485, 956, 521),
    "RF33": (991, 163, 1075, 199), "RF36": (991, 219, 1075, 255), "RF35": (991, 274, 1075, 310), "RF34": (991, 329, 1075, 365),
    "RF40": (991, 382, 1075, 418), "RF37": (991, 437, 1075, 473), "RF38": (991, 492, 1075, 528),
    "RF46": (758, 309, 842, 345), "RF19": (758, 370, 842, 406), "RF26": (800, 428, 834, 505),
    "RF41": (84, 398, 118, 476), "RF47": (87, 555, 171, 591), "RSB11": (755, 533, 843, 607),
    "RSB01": (607, 620, 681, 715), "RSB08": (686, 620, 760, 715), "RSB05": (765, 620, 839, 715), "RSB07": (844, 620, 918, 715),
    "RF07": (949, 627, 983, 703), "RF32": (1004, 627, 1038, 703), "RF45": (1059, 627, 1093, 703),
    "RF05": (128, 678, 162, 755), "RF06": (183, 678, 217, 755), "RF04": (238, 678, 272, 755), "RF44": (293, 678, 327, 755),
    "RF16": (348, 678, 382, 755), "RF10": (68, 790, 152, 826),
    "RF17": (128, 848, 162, 925), "RF11": (183, 848, 217, 925), "RF21": (238, 848, 272, 925), "RF12": (293, 848, 327, 925),
    "RF09": (348, 848, 382, 925), "RF13": (403, 848, 437, 925),
    "RSB02": (552, 733, 642, 800), "RF39": (1054, 762, 1088, 838),
    "RF14": (656, 893, 690, 968), "RF08": (711, 893, 745, 968), "RF01": (766, 893, 800, 968), "RF22": (821, 893, 855, 968),
    "RSB09": (890, 890, 965, 988), "RSB04": (970, 890, 1045, 988), "RF15": (1070, 893, 1104, 968),
}
# What else is on that face, to find one's way: the two connectors, the plain plate and the moulded mark
_CABIN_MARKS: List[Tuple[str, Tuple[int, int, int, int]]] = [
    ("C4", (148, 250, 503, 468)), ("C2", (672, 738, 1022, 862)), ("", (194, 491, 585, 647)), ("9AR", (615, 365, 735, 440)),
]
_CABIN_VIEW: Text = ("La caja vista de frente: el logotipo de Chery y «9AR» están grabados en ella, y C4 y C2 son sus dos conectores.",
                     "The box seen from the front: the Chery logo and «9AR» are moulded in it, and C4 and C2 are its two connectors.",
                     "A caixa vista de frente: o logótipo da Chery e «9AR» estão gravados nela, e C4 e C2 são os seus dois conectores.")

_TABLE_EMPTY: Text = ("La tabla del manual deja esta posición vacía; su dibujo le pone este rótulo.", "The manual's table leaves this position empty; its drawing carries this label.",
                      "A tabela do manual deixa esta posição vazia; o desenho tem este rótulo.")
_ENGINE: List[Row] = [
    ("SB01", 60, ("Freno integrado (IPB)", "Integrated brake unit (IPB)", "Travão integrado (IPB)"), None),
    ("SB02", 20, ("Accesorios (ACC)", "Accessories (ACC)", "Acessórios (ACC)"), None),
    ("SB03", 30, ("Arranque", "Start", "Arranque"), None),
    ("SB04", 30, ("Portón trasero eléctrico", "Power tailgate", "Porta da bagageira elétrica"), None),
    ("SB05", 30, _bcm(7), None), ("SB06", 30, _bcm(8), None),
    ("SB07", 40, ("Freno de estacionamiento eléctrico (EPB) y ABS", "Electric parking brake (EPB) and ABS", "Travão de estacionamento elétrico (EPB) e ABS"), None),
    ("SB08", 40, ("Freno de estacionamiento eléctrico (EPB)", "Electric parking brake (EPB)", "Travão de estacionamento elétrico (EPB)"), None),
    ("SB09", 40, ("Manillas de puerta escamoteables", "Flush door handles", "Puxadores de porta retráteis"), None),
    ("SB10", 30, ("Transmisión (rotulado «DCT»)", "Transmission (labelled «DCT»)", "Transmissão (rótulo «DCT»)"), None),
    ("SB11", 30, ("Ajuste de asiento", "Seat adjustment", "Regulação do banco"), None),
    ("EF01", 20, ("Ventilador del radiador de aceite", "Oil cooler fan", "Ventilador do radiador de óleo"), None),
    ("EF02", 15, ("Sonda lambda", "Oxygen sensor", "Sonda lambda"), None),
    ("EF03", 15, ("Bobinas de encendido", "Ignition coils", "Bobinas de ignição"), None),
    ("EF04", 15, ("Válvulas del motor (VCP, CCP, CVH)", "Engine valves (VCP, CCP, CVH)", "Válvulas do motor (VCP, CCP, CVH)"), None),
    ("EF05", 20, ("Tracción total (AWD)", "All-wheel drive (AWD)", "Tração integral (AWD)"), _TABLE_EMPTY),
    ("EF06", 10, ("Ventilador de refrigeración (PWM)", "Cooling fan (PWM)", "Ventilador de arrefecimento (PWM)"),
     ("La tabla del manual dice «interruptor de freno»; su dibujo, «PWM FAN».", "The manual's table says «brake switch»; its drawing, «PWM FAN».",
      "A tabela do manual diz «interruptor de travão»; o desenho, «PWM FAN».")),
    ("EF07", 15, ("Lavafaros", "Headlamp washer", "Lava-faróis"), None),
    ("EF08", 20, ("Accesorios 2 (ACC2)", "Accessories 2 (ACC2)", "Acessórios 2 (ACC2)"), None),
    ("EF09", 25, ("Ajuste del asiento del acompañante", "Front passenger seat adjustment", "Regulação do banco do passageiro"), None),
    ("EF10", 15, ("Centralita del motor (ECU) y sistema de vapores del depósito (DMTL, FTIV)", "Engine control unit (ECU) and fuel tank vapour system (DMTL, FTIV)",
                  "Unidade de controlo do motor (ECU) e sistema de vapores do depósito (DMTL, FTIV)"), None),
    ("EF11", 10, ("Gestión de la batería de alta tensión (BMS 1 y 2)", "High-voltage battery management (BMS 1 and 2)", "Gestão da bateria de alta tensão (BMS 1 e 2)"),
     ("La tabla del manual dice «BDM1/BDM2»; su dibujo, «BMS1&BMS2».", "The manual's table says «BDM1/BDM2»; its drawing, «BMS1&BMS2».", "A tabela do manual diz «BDM1/BDM2»; o desenho, «BMS1&BMS2».")),
    ("EF12", None, ("Faro adaptativo derecho (ADB)", "Right adaptive headlamp (ADB)", "Farol adaptativo direito (ADB)"),
     ("La tabla del manual dice 10 A y su dibujo 20 A (el izquierdo, EF35, es de 20 A). Mira el fusible que lleva puesto.",
      "The manual's table says 10 A and its drawing 20 A (the left one, EF35, is 20 A). Look at the fuse that is fitted.",
      "A tabela do manual diz 10 A e o desenho 20 A (o esquerdo, EF35, é de 20 A). Vê o fusível que está montado.")),
    ("EF13", 10, ("Centralitas del sistema híbrido (VCU, MCU, CDU y BMS), positivo de contacto", "Hybrid system control units (VCU, MCU, CDU and BMS), ignition feed",
                  "Unidades de controlo do sistema híbrido (VCU, MCU, CDU e BMS), positivo de ignição"), None),
    ("EF14", 7.5, ("Cámaras 360°, ángulo muerto, aviso de carril, frenada de emergencia, control de crucero, cámara de a bordo, retrovisor electrocrómico, telepeaje y módulo de faros",
                   "360° cameras, blind spot, lane warning, emergency braking, cruise control, dash camera, auto-dimming mirror, toll tag and headlamp module",
                   "Câmaras 360°, ângulo morto, aviso de faixa, travagem de emergência, cruise control, câmara de bordo, retrovisor eletrocrómico, portagem eletrónica e módulo de faróis"), None),
    ("EF15", 7.5, ("Ayudas a la conducción (cámaras, ángulo muerto, carril, frenada, crucero), positivo de contacto", "Driver assistance (cameras, blind spot, lane, braking, cruise), ignition feed",
                   "Ajudas à condução (câmaras, ângulo morto, faixa, travagem, cruise), positivo de ignição"), None),
    ("EF16", 15, ("Ventilador trasero", "Rear blower", "Ventilador traseiro"), None),
    ("EF17", 20, ("Ventilador de la 2.ª fila", "Second-row blower", "Ventilador da 2.ª fila"), None),
    ("EF18", 15, ("Gestión de la batería de alta tensión (BMS 3 y 4)", "High-voltage battery management (BMS 3 and 4)", "Gestão da bateria de alta tensão (BMS 3 e 4)"), None),
    ("EF19", 7.5, ("Parrilla activa", "Active grille shutter", "Grelha ativa"), None),
    ("EF20", 15, ("Inyectores", "Injectors", "Injetores"), None),
    ("EF21", 10, None, None), ("EF22", 10, None, None),
    ("EF23", 10, ("Bomba de agua de la batería de alta tensión", "High-voltage battery coolant pump", "Bomba de água da bateria de alta tensão"), None),
    ("EF24", 15, ("Reserva para el lavado delantero y trasero", "Reserved for front and rear washing", "Reserva para a lavagem dianteira e traseira"), None),
    ("EF25", 15, _bcm(1), None),
    ("EF26", 7.5, None, None),
    ("EF27", 20, ("Bocina", "Horn", "Buzina"), None),
    ("EF28", 7.5, ("ESP, ABS y freno integrado (IPB)", "ESP, ABS and integrated brake unit (IPB)", "ESP, ABS e travão integrado (IPB)"), None),
    ("EF29", 20, ("Bomba de combustible", "Fuel pump", "Bomba de combustível"), None),
    ("EF30", 25, _bcm(5), None),
    ("EF31", 20, ("Contacto (IGN1)", "Ignition (IGN1)", "Ignição (IGN1)"), None),
    ("EF32", 10, ("Motores eléctricos delantero y trasero (MCU), radar frontal, llave NFC, mando de ventanillas del conductor, portón eléctrico, toma de carga lenta, interruptor de freno y aparcamiento asistido",
                  "Front and rear motor controllers (MCU), front radar, NFC key, driver window switch, power tailgate, slow-charge port, brake switch and parking assist",
                  "Motores elétricos dianteiro e traseiro (MCU), radar frontal, chave NFC, comando dos vidros do condutor, porta da bagageira elétrica, tomada de carga lenta, interruptor de travão e estacionamento assistido"), None),
    ("EF33", 25, _bcm(4), None), ("EF34", 15, _bcm(9), None),
    ("EF35", 20, ("Faro adaptativo izquierdo (ADB)", "Left adaptive headlamp (ADB)", "Farol adaptativo esquerdo (ADB)"), None),
    ("EF36", 15, ("Bomba de aceite del eje trasero", "Rear drive unit oil pump", "Bomba de óleo do eixo traseiro"), None),
    ("EF37", 15, ("Alimentación de reserva trasera", "Rear spare power supply", "Alimentação de reserva traseira"), None),
    ("EF38", 15, ("Centralitas del motor, del sistema híbrido y del cambio (ECU, HCU, TCU)", "Engine, hybrid system and transmission control units (ECU, HCU, TCU)",
                  "Unidades de controlo do motor, do sistema híbrido e da transmissão (ECU, HCU, TCU)"), None),
    ("EF39", 25, ("Limpiaparabrisas", "Wipers", "Limpa-para-brisas"), None),
    ("EF40", 15, ("Motor de la tracción total", "All-wheel drive motor", "Motor da tração integral"), _TABLE_EMPTY),
    ("EF41", 20, ("Calefacción de los asientos", "Seat heating", "Aquecimento dos bancos"), None),
    ("EF42", 10, ("Válvula de agua, interruptor de freno y centralitas del motor (ECU) y del sistema híbrido (HCU)", "Coolant valve, brake switch and engine (ECU) and hybrid system (HCU) control units",
                  "Válvula de água, interruptor de travão e unidades de controlo do motor (ECU) e do sistema híbrido (HCU)"), None),
    ("EF43", 15, ("Bomba de agua 2", "Coolant pump 2", "Bomba de água 2"), None),
    ("EF44", 30, _bcm(2), None),
    ("EF45", 30, ("Techo solar", "Sunroof", "Teto de abrir"), None),
    ("EF46", 25, ("Fusible de transporte (memoria)", "Transport fuse (memory)", "Fusível de transporte (memória)"), None),
    ("EF47", 100, ("Alimentación de la caja de fusibles del habitáculo", "Supply to the cabin fuse box", "Alimentação da caixa de fusíveis do habitáculo"), None),
    ("EF48", 80, ("Dirección asistida eléctrica (EPS)", "Electric power steering (EPS)", "Direção assistida elétrica (EPS)"), None),
    ("EF49", 175, ("Convertidor DC-DC (carga la batería de 12 V)", "DC-DC converter (charges the 12 V battery)", "Conversor DC-DC (carrega a bateria de 12 V)"), None),
    ("EF50", 80, ("Ventilador de refrigeración", "Cooling fan", "Ventilador de arrefecimento"), None),
    ("EF51", 80, ("Bomba de agua del motor", "Engine coolant pump", "Bomba de água do motor"), None),
    ("EF52", 50, ("Parabrisas térmico 2 / calefactor PTC 2", "Heated windscreen 2 / PTC heater 2", "Para-brisas aquecido 2 / aquecedor PTC 2"), None),
    ("EF53", 50, ("Parabrisas térmico 1 / calefactor PTC 1", "Heated windscreen 1 / PTC heater 1", "Para-brisas aquecido 1 / aquecedor PTC 1"), None),
    ("EF54", 60, ("Freno integrado (IPB)", "Integrated brake unit (IPB)", "Travão integrado (IPB)"), None),
    ("EF55", 60, ("Bomba de aceite eléctrica (EOP)", "Electric oil pump (EOP)", "Bomba de óleo elétrica (EOP)"), None),
]
_ENGINE_RELAYS: List[Tuple[str, Text]] = [
    ("ERLY01", ("Ventilador del radiador de aceite", "Oil cooler fan", "Ventilador do radiador de óleo")),
    ("ERLY02", ("Relé principal", "Main relay", "Relé principal")),
    ("ERLY03", ("Lavafaros", "Headlamp washer", "Lava-faróis")),
    ("ERLY04", ("Accesorios 2 (ACC2)", "Accessories 2 (ACC2)", "Acessórios 2 (ACC2)")),
    ("ERLY05", ("Contacto (IGN1)", "Ignition (IGN1)", "Ignição (IGN1)")),
    ("ERLY06", ("Ventilador trasero", "Rear blower", "Ventilador traseiro")),
    ("ERLY07", ("Ventilador de la 2.ª fila", "Second-row blower", "Ventilador da 2.ª fila")),
    ("ERLY08", SPARE), ("ERLY09", ("Bomba de aceite eléctrica (EOP)", "Electric oil pump (EOP)", "Bomba de óleo elétrica (EOP)")),
    ("ERLY10", SPARE), ("ERLY11", SPARE),
    ("ERLY12", ("Bocina", "Horn", "Buzina")),
    ("ERLY13", ("Bomba de combustible", "Fuel pump", "Bomba de combustível")),
    ("ERLY14", ("Accesorios (ACC)", "Accessories (ACC)", "Acessórios (ACC)")),
    ("ERLY15", ("Arranque", "Start", "Arranque")),
    ("ERLY16", ("Centralita del sistema híbrido (HCU)", "Hybrid system control unit (HCU)", "Unidade de controlo do sistema híbrido (HCU)")),
    ("ERLY17", ("Parabrisas térmico 2 / calefactor PTC 2", "Heated windscreen 2 / PTC heater 2", "Para-brisas aquecido 2 / aquecedor PTC 2")),
    ("ERLY18", ("Parabrisas térmico 1 / calefactor PTC 1", "Heated windscreen 1 / PTC heater 1", "Para-brisas aquecido 1 / aquecedor PTC 1")),
]

# Engine-bay box: the manual's own layout drawing (a vector drawing, so these are its exact rectangles), fuses and relays alike.
_ENGINE_FRAME = (776, 83, 1313, 716)
_ENGINE_LAYOUT: Dict[str, Tuple[int, int, int, int]] = {
    "EF54": (789, 90, 868, 124), "EF47": (789, 152, 868, 186), "EF48": (789, 214, 868, 248), "EF49": (789, 276, 868, 310),
    "EF52": (789, 338, 868, 372), "EF55": (789, 400, 868, 434), "EF50": (789, 525, 868, 558), "EF53": (789, 587, 868, 621),
    "EF51": (789, 649, 868, 683), "SB11": (871, 254, 916, 288), "EF46": (878, 650, 946, 676), "ERLY15": (886, 90, 950, 150),
    "ERLY16": (884, 167, 952, 234), "SB10": (919, 254, 964, 288), "ERLY17": (880, 326, 948, 394), "ERLY18": (880, 550, 948, 618),
    "EF42": (958, 160, 1020, 186), "EF43": (958, 193, 1020, 219), "SB05": (958, 294, 1003, 328), "SB06": (958, 345, 1003, 379),
    "EF44": (959, 399, 985, 461), "SB03": (968, 107, 1011, 154), "SB04": (968, 231, 1011, 278), "EF45": (990, 399, 1016, 461),
    "SB07": (965, 488, 1007, 534), "SB08": (965, 550, 1007, 596), "SB09": (965, 612, 1007, 659), "ERLY10": (1030, 90, 1094, 150),
    "ERLY11": (1032, 166, 1080, 214), "EF40": (1018, 234, 1044, 296), "ERLY14": (1030, 360, 1095, 420), "EF41": (1025, 431, 1051, 493),
    "SB02": (1016, 505, 1061, 539), "ERLY12": (1051, 238, 1099, 286), "ERLY13": (1051, 301, 1099, 348), "EF37": (1074, 431, 1100, 493),
    "EF38": (1074, 517, 1100, 579), "EF39": (1074, 588, 1100, 650), "EF19": (1104, 91, 1166, 117), "EF20": (1104, 121, 1166, 147),
    "EF21": (1104, 151, 1166, 177), "EF22": (1104, 181, 1166, 207), "EF23": (1104, 211, 1166, 237), "EF24": (1104, 251, 1167, 277),
    "EF25": (1104, 281, 1167, 307), "EF26": (1104, 311, 1167, 337), "EF27": (1104, 341, 1167, 367), "EF28": (1104, 371, 1167, 397),
    "EF29": (1104, 410, 1167, 436), "EF30": (1105, 440, 1167, 466), "EF31": (1105, 470, 1167, 496), "EF32": (1105, 500, 1167, 526),
    "EF33": (1104, 541, 1167, 566), "EF34": (1104, 570, 1167, 596), "EF35": (1104, 600, 1167, 626), "EF36": (1104, 630, 1167, 656),
    "EF01": (1172, 91, 1234, 117), "EF02": (1172, 121, 1234, 147), "EF03": (1172, 151, 1234, 177), "EF04": (1172, 181, 1234, 207),
    "EF05": (1172, 211, 1234, 237), "EF06": (1172, 251, 1234, 277), "EF07": (1172, 281, 1234, 307), "EF08": (1172, 311, 1234, 337),
    "EF09": (1172, 341, 1234, 367), "EF10": (1172, 371, 1234, 397), "EF11": (1172, 410, 1234, 436), "EF12": (1172, 440, 1234, 466),
    "EF13": (1172, 470, 1234, 496), "EF14": (1172, 500, 1234, 526), "EF15": (1172, 541, 1234, 566), "EF16": (1172, 570, 1234, 596),
    "EF17": (1172, 600, 1234, 626), "EF18": (1172, 630, 1234, 656), "ERLY01": (1239, 97, 1304, 157), "ERLY02": (1239, 165, 1304, 224),
    "ERLY03": (1239, 232, 1304, 292), "ERLY04": (1239, 300, 1304, 360), "ERLY05": (1239, 368, 1304, 428), "ERLY06": (1239, 435, 1304, 495),
    "ERLY07": (1239, 503, 1304, 563), "ERLY08": (1239, 571, 1304, 631), "SB01": (1202, 661, 1245, 708), "ERLY09": (1252, 651, 1300, 699),
}
# The battery feed stud and the holder of the fuse puller (drawn without a label)
_ENGINE_MARKS: List[Tuple[str, Tuple[int, int, int, int]]] = [("B+", (834, 478, 878, 520)), ("", (1007, 318, 1043, 352))]


# ───────────────────────── Ebro S800 PHEV ─────────────────────────
# Source: the owner's manual of the CAOA Chery Tiggo 8 Pro plug-in hybrid (Brazil, 2026/27 edition), the S800's car: same engine
# and gearbox (H4J15 + 130HHB) and 176 of its 181 illustrations are in Ebro's S800 manual, the fuse ones included (OMT18-5111…5115).
# It prints a drawing and a table of each box; they agree almost everywhere. Its table lists only the positions in use, so a
# position that only the drawing carries may not be fitted on this car: it says so. The 2025/26 edition is the previous car (other
# engine and gearbox) and is not used. That manual speaks of front-wheel drive only.
_S800_DRAWING_ONLY: Text = ("Solo la trae el dibujo del manual; su tabla, que lista las posiciones en uso, no la menciona: puede que tu coche no la lleve.",
                            "Only the manual's drawing has it; its table, which lists the positions in use, does not: your car may not have it.",
                            "Só o desenho do manual a tem; a tabela, que lista as posições em uso, não a menciona: pode ser que o teu carro não a tenha.")
_S800_NO_FUNCTION: Text = ("El dibujo del manual pone aquí un fusible sin decir qué alimenta, y su tabla no lo menciona.",
                           "The manual's drawing fits a fuse here without saying what it feeds, and its table does not mention it.",
                           "O desenho do manual põe aqui um fusível sem dizer o que alimenta, e a tabela não o menciona.")


def _misprint(printed: str) -> Text:
    """The drawing labels this cell with another position's name (the table and the place say which one it is)."""
    return (f"El dibujo del manual rotula esta posición como {printed} por error; por su sitio y la tabla es esta.",
            f"The manual's drawing labels this position {printed} by mistake; its place and the table say it is this one.",
            f"O desenho do manual rotula esta posição como {printed} por engano; pelo sítio e pela tabela é esta.")


def _t(es: str, en: str, pt: str) -> Text:
    return (es, en, pt)


_S800_ENGINE: List[Row] = [
    ("SB01", 60, _t("Freno integrado (IPB 2)", "Integrated brake unit (IPB 2)", "Travão integrado (IPB 2)"),
     _t("La tabla del manual dice «alimentación del BCM 1»; su dibujo, «IPB2».", "The manual's table says «BCM supply 1»; its drawing, «IPB2».",
        "A tabela do manual diz «alimentação do BCM 1»; o desenho, «IPB2».")),
    ("SB02", None, _t("Accesorios (ACC)", "Accessories (ACC)", "Acessórios (ACC)"),
     _t("La tabla del manual dice 20 A; su dibujo, 60 A, y rotula esta posición como SB01 por error. Mira el fusible que lleva puesto.",
        "The manual's table says 20 A; its drawing, 60 A, and labels this position SB01 by mistake. Look at the fuse that is fitted.",
        "A tabela do manual diz 20 A; o desenho, 60 A, e rotula esta posição como SB01 por engano. Vê o fusível que está montado.")),
    ("SB03", 30, _t("Arranque", "Start", "Arranque"), _S800_DRAWING_ONLY),
    ("SB04", 30, _t("Módulo de puertas (DCM)", "Door module (DCM)", "Módulo das portas (DCM)"), _S800_DRAWING_ONLY),
    ("SB05", 30, _bcm(7), None), ("SB06", 30, _bcm(8), None),
    ("SB07", 40, _t("ESP y ABS", "ESP and ABS", "ESP e ABS"), _S800_DRAWING_ONLY),
    ("SB08", 40, _t("Freno de estacionamiento eléctrico (EPB) 1", "Electric parking brake (EPB) 1", "Travão de estacionamento elétrico (EPB) 1"), None),
    ("SB09", 30, _t("Freno de estacionamiento eléctrico (EPB) 2", "Electric parking brake (EPB) 2", "Travão de estacionamento elétrico (EPB) 2"), None),
    ("SB10", 30, _t("Ajuste del asiento del acompañante", "Front passenger seat adjustment", "Regulação do banco do passageiro"), None),
    ("SB11", 30, _t("Ajuste del asiento del conductor", "Driver seat adjustment", "Regulação do banco do condutor"), _S800_DRAWING_ONLY),
    ("EF01", 25, _t("Lavafaros", "Headlamp washer", "Lava-faróis"), _S800_DRAWING_ONLY),
    ("EF02", 15, _t("Sondas lambda (antes y después del catalizador)", "Oxygen sensors (before and after the catalyst)", "Sondas lambda (antes e depois do catalisador)"), None),
    ("EF03", 15, _t("Bobinas de encendido", "Ignition coils", "Bobinas de ignição"), None),
    ("EF04", 15, _t("Válvulas del motor (ventilación del cárter, refrigeración de los pistones y cánister)", "Engine valves (crankcase ventilation, piston cooling and canister)",
                    "Válvulas do motor (ventilação do cárter, arrefecimento dos pistões e canister)"), None),
    ("EF05", 15, _t("Bloqueo electrónico del diferencial (EDL; opcional)", "Electronic differential lock (EDL; optional)", "Bloqueio eletrónico do diferencial (EDL; opcional)"), _S800_DRAWING_ONLY),
    ("EF06", 10, _t("Relés del ventilador de refrigeración y de la bomba de combustible", "Cooling fan and fuel pump relays", "Relés do ventilador de arrefecimento e da bomba de combustível"), None),
    ("EF07", 15, _t("Ventilador del radiador de aceite", "Oil cooler fan", "Ventilador do radiador de óleo"), _S800_DRAWING_ONLY),
    ("EF08", 20, _t("Accesorios 2 (ACC2) y calefacción de los asientos delanteros y del derecho de la 2.ª fila", "Accessories 2 (ACC2) and heating of the front seats and the right 2nd-row seat",
                    "Acessórios 2 (ACC2) e aquecimento dos bancos dianteiros e do direito da 2.ª fila"), None),
    ("EF09", 25, _t("Faro izquierdo", "Left headlamp", "Farol esquerdo"), None),
    ("EF10", None, _t("Centralita del motor y sistema de vapores del depósito (ESA, DMTL, FTIV)", "Engine control unit and fuel tank vapour system (ESA, DMTL, FTIV)",
                      "Unidade de controlo do motor e sistema de vapores do depósito (ESA, DMTL, FTIV)"),
     _t("La tabla del manual dice 10 A y su dibujo 15 A. Mira el fusible que lleva puesto.", "The manual's table says 10 A and its drawing 15 A. Look at the fuse that is fitted.",
        "A tabela do manual diz 10 A e o desenho 15 A. Vê o fusível que está montado.")),
    ("EF11", 10, None, None),
    ("EF12", 25, _t("Faro derecho", "Right headlamp", "Farol direito"), None),
    ("EF13", 10, _t("Centralitas del motor y del sistema híbrido (ESA, BMS, CCU, motores delantero y trasero, HCU)", "Engine and hybrid system control units (ESA, BMS, CCU, front and rear motors, HCU)",
                    "Unidades de controlo do motor e do sistema híbrido (ESA, BMS, CCU, motores dianteiro e traseiro, HCU)"), None),
    ("EF14", 7.5, _t("Conectores de los asientos delanteros, telepeaje, sensor de aparcamiento trasero, cámara de a bordo, retrovisor electrocrómico y mando del ventilador trasero",
                     "Front seat connectors, toll tag, rear parking sensor, dash camera, auto-dimming mirror and rear blower control",
                     "Conectores dos bancos dianteiros, portagem eletrónica, sensor de estacionamento traseiro, câmara de bordo, retrovisor eletrocrómico e comando do ventilador traseiro"), None),
    ("EF15", 7.5, _t("Amplificador, aviso de colisión frontal (FCW) y módulos de los faros", "Amplifier, forward collision warning (FCW) and headlamp modules",
                     "Amplificador, aviso de colisão frontal (FCW) e módulos dos faróis"), None),
    ("EF16", 15, _t("Climatización trasera (ventilador trasero)", "Rear climate control (rear blower)", "Climatização traseira (ventilador traseiro)"), None),
    ("EF17", 15, _t("Volante calefactable", "Heated steering wheel", "Volante aquecido"), None),
    ("EF18", 10, _t("Gestión de la batería de alta tensión (BMS)", "High-voltage battery management (BMS)", "Gestão da bateria de alta tensão (BMS)"), None),
    ("EF19", 7.5, _t("Parrilla activa", "Active grille shutter", "Grelha ativa"), _S800_DRAWING_ONLY),
    ("EF20", 15, _t("Inyectores", "Injectors", "Injetores"), _S800_DRAWING_ONLY),
    ("EF21", 15, None, None),
    ("EF22", 20, _t("Bomba de agua", "Coolant pump", "Bomba de água"), None),
    ("EF23", 15, _t("Bomba de agua de la batería, electroválvula de refrigeración 2, calefactor eléctrico, válvula de expansión, sensor de la tapa del depósito y ventilador",
                    "Battery coolant pump, cooling solenoid valve 2, electric heater, expansion valve, fuel flap sensor and fan",
                    "Bomba de água da bateria, eletroválvula de arrefecimento 2, aquecedor elétrico, válvula de expansão, sensor da tampa do depósito e ventilador"), None),
    ("EF24", 15, _t("Lavaparabrisas delantero y trasero", "Front and rear washers", "Lava-vidros dianteiro e traseiro"), None),
    ("EF25", 15, _bcm(1), None),
    ("EF26", 7.5, _t("Airbags", "Airbags", "Airbags"), None),
    ("EF27", 20, _t("Bocina", "Horn", "Buzina"), None),
    ("EF28", 7.5, _t("Freno de estacionamiento eléctrico (EPB) y freno integrado (IPB)", "Electric parking brake (EPB) and integrated brake unit (IPB)",
                     "Travão de estacionamento elétrico (EPB) e travão integrado (IPB)"), None),
    ("EF29", 20, _t("Bomba de combustible", "Fuel pump", "Bomba de combustível"), None),
    ("EF30", 25, _bcm(5), None),
    ("EF31", 20, _t("Contacto (IGN1)", "Ignition (IGN1)", "Ignição (IGN1)"), None),
    ("EF32", 10, _t("Interruptor de freno, portón eléctrico, mando de la ventanilla delantera izquierda, ángulo muerto, toma de carga y motores eléctricos",
                    "Brake switch, power tailgate, front left window switch, blind spot, charging port and electric motors",
                    "Interruptor de travão, porta da bagageira elétrica, comando do vidro dianteiro esquerdo, ângulo morto, tomada de carga e motores elétricos"), None),
    ("EF33", 25, _bcm(4), None), ("EF34", 15, _bcm(9), None),
    ("EF35", 25, _t("Portón trasero eléctrico", "Power tailgate", "Porta da bagageira elétrica"), None),
    ("EF36", 15, _t("Centralitas del sistema híbrido, del cambio y de carga (HCU, TCU, CCU)", "Hybrid system, transmission and charging control units (HCU, TCU, CCU)",
                    "Unidades de controlo do sistema híbrido, da transmissão e de carga (HCU, TCU, CCU)"), None),
    ("EF37", 15, _t("Toma de corriente trasera, ventilador trasero y ACC2", "Rear power socket, rear blower and ACC2", "Tomada de corrente traseira, ventilador traseiro e ACC2"), None),
    ("EF38", 10, _t("Centralita del motor (ECU) y gestión de la batería (BMS)", "Engine control unit (ECU) and battery management (BMS)",
                    "Unidade de controlo do motor (ECU) e gestão da bateria (BMS)"), None),
    ("EF39", 25, _t("Limpiaparabrisas", "Wipers", "Limpa-para-brisas"), None),
    ("EF40", 10, _t("Interruptor de freno, centralitas del motor y del sistema híbrido y electroválvula de refrigeración 3", "Brake switch, engine and hybrid control units and cooling solenoid valve 3",
                    "Interruptor de travão, unidades de controlo do motor e do sistema híbrido e eletroválvula de arrefecimento 3"), None),
    ("EF41", 20, _t("Calefacción de los asientos delanteros y del izquierdo de la 2.ª fila", "Heating of the front seats and the left 2nd-row seat",
                    "Aquecimento dos bancos dianteiros e do esquerdo da 2.ª fila"), None),
    ("EF42", 15, _t("Bomba de agua", "Coolant pump", "Bomba de água"), _S800_DRAWING_ONLY),
    ("EF43", 15, _t("Compresor del aire acondicionado, electroválvula de refrigeración 1 y bomba de agua del aire acondicionado",
                    "Air-conditioning compressor, cooling solenoid valve 1 and air-conditioning coolant pump",
                    "Compressor do ar condicionado, eletroválvula de arrefecimento 1 e bomba de água do ar condicionado"), None),
    ("EF44", 30, _bcm(2), _misprint("EF45")),
    ("EF45", 25, _t("Techo solar", "Sunroof", "Teto de abrir"), None),
    ("EF46", 25, _t("Fusible de transporte (memoria)", "Transport fuse (memory)", "Fusível de transporte (memória)"), _S800_DRAWING_ONLY),
    ("EF47", 100, _t("Alimentación de la caja de fusibles del habitáculo", "Supply to the cabin fuse box", "Alimentação da caixa de fusíveis do habitáculo"), None),
    ("EF48", 80, _t("Dirección asistida eléctrica (EPS)", "Electric power steering (EPS)", "Direção assistida elétrica (EPS)"), None),
    ("EF49", None, None, None),
    ("EF50", 125, _t("Ventilador de refrigeración", "Cooling fan", "Ventilador de arrefecimento"), None),
    ("EF52", 50, _t("Parabrisas térmico, lado izquierdo", "Heated windscreen, left side", "Para-brisas aquecido, lado esquerdo"), None),
    ("EF53", 50, _t("Parabrisas térmico, lado derecho", "Heated windscreen, right side", "Para-brisas aquecido, lado direito"), _misprint("EF52")),
    ("EF54", 60, _t("Freno integrado (IPB)", "Integrated brake unit (IPB)", "Travão integrado (IPB)"), None),
    ("EF55", 80, _t("Bomba de agua eléctrica del motor", "Electric engine coolant pump", "Bomba de água elétrica do motor"), None),
]
_S800_ENGINE_RELAYS: List[Tuple[str, Text]] = [
    ("ERLY01", _t("Lavafaros (reserva)", "Headlamp washer (spare)", "Lava-faróis (reserva)")),
    ("ERLY02", _t("Centralita del motor (ECU)", "Engine control unit (ECU)", "Unidade de controlo do motor (ECU)")),
    ("ERLY03", _t("Ventilador del radiador de aceite", "Oil cooler fan", "Ventilador do radiador de óleo")),
    ("ERLY04", _t("Accesorios 2 (ACC2)", "Accessories 2 (ACC2)", "Acessórios 2 (ACC2)")),
    ("ERLY05", _t("Contacto (IGN1)", "Ignition (IGN1)", "Ignição (IGN1)")),
    ("ERLY06", _t("Ventilador trasero", "Rear blower", "Ventilador traseiro")),
    ("ERLY07", _t("Volante calefactable", "Heated steering wheel", "Volante aquecido")),
    ("ERLY08", SPARE), ("ERLY10", SPARE),
    ("ERLY12", _t("Bocina", "Horn", "Buzina")),
    ("ERLY13", _t("Bomba de combustible", "Fuel pump", "Bomba de combustível")),
    ("ERLY14", _t("Accesorios (ACC)", "Accessories (ACC)", "Acessórios (ACC)")),
    ("ERLY15", _t("Arranque", "Start", "Arranque")),
    ("ERLY16", _t("Centralita del sistema híbrido (HCU)", "Hybrid system control unit (HCU)", "Unidade de controlo do sistema híbrido (HCU)")),
    ("ERLY17", _t("Parabrisas térmico 2", "Heated windscreen 2", "Para-brisas aquecido 2")),
    ("ERLY18", _t("Parabrisas térmico 1", "Heated windscreen 1", "Para-brisas aquecido 1")),
    ("ERLY19", _t("Bomba del lavaparabrisas delantero", "Front washer pump", "Bomba do lava-vidros dianteiro")),
    ("ERLY20", _t("Bomba del lavaluneta", "Rear washer pump", "Bomba do lava-vidros traseiro")),
    ("ERLY21", _t("Limpiaparabrisas, velocidad lenta", "Wipers, low speed", "Limpa-para-brisas, velocidade lenta")),
    ("ERLY22", _t("Limpiaparabrisas, velocidad rápida", "Wipers, high speed", "Limpa-para-brisas, velocidade rápida")),
]
# Where each one sits: rectangles read off the manual's layout drawing (pixels of that picture). ERLY05 and ERLY19-22 are in the
# table but not on the drawing; the drawing's unlabelled cells are kept as plain marks.
_S800_ENGINE_FRAME = (28, 40, 1057, 1444)
_S800_ENGINE_LAYOUT: Dict[str, Tuple[int, int, int, int]] = {
    "EF54": (39, 59, 194, 126), "EF47": (38, 189, 199, 259), "EF48": (39, 320, 199, 390), "EF49": (39, 458, 193, 523),
    "EF52": (39, 593, 195, 665), "EF55": (39, 726, 195, 794), "EF50": (39, 1108, 198, 1176), "EF53": (38, 1248, 198, 1314),
    "ERLY15": (225, 49, 364, 161), "ERLY16": (211, 174, 400, 365), "ERLY17": (203, 572, 383, 780), "ERLY18": (211, 1134, 394, 1344),
    "EF45": (201, 411, 259, 529), "EF44": (269, 411, 326, 529), "SB11": (337, 422, 401, 523), "EF46": (241, 1367, 389, 1428),
    "EF42": (404, 100, 535, 164), "SB03": (410, 202, 509, 270), "EF43": (408, 296, 516, 361), "SB04": (412, 391, 527, 480),
    "SB05": (408, 487, 474, 594), "SB10": (491, 487, 559, 593), "SB06": (392, 730, 487, 793), "EF41": (344, 885, 474, 947),
    "EF38": (344, 993, 478, 1055), "EF37": (498, 993, 651, 1055), "SB07": (411, 1109, 515, 1205), "SB08": (411, 1257, 515, 1354),
    "SB02": (535, 1089, 604, 1173), "SB09": (526, 1189, 608, 1303), "EF39": (535, 1322, 654, 1381),
    "ERLY10": (580, 145, 670, 302), "EF40": (543, 354, 670, 416), "ERLY12": (572, 465, 668, 564), "ERLY13": (569, 621, 668, 721),
    "ERLY14": (494, 752, 667, 946),
    "EF19": (679, 59, 804, 126), "EF20": (679, 136, 804, 199), "EF21": (678, 207, 804, 270), "EF22": (679, 278, 804, 342),
    "EF23": (679, 353, 804, 416), "EF24": (679, 438, 804, 500), "EF26": (679, 534, 803, 597), "EF27": (679, 607, 803, 671),
    "EF28": (679, 679, 803, 741), "EF29": (675, 763, 802, 826), "EF30": (676, 837, 803, 899), "EF31": (675, 908, 802, 969),
    "EF32": (672, 981, 802, 1042), "EF33": (681, 1088, 803, 1149), "EF34": (681, 1158, 802, 1218), "EF35": (681, 1229, 802, 1289),
    "EF36": (681, 1300, 802, 1360),
    "EF01": (813, 59, 930, 126), "EF02": (813, 136, 931, 199), "EF03": (813, 207, 930, 270), "EF04": (813, 278, 931, 341),
    "EF05": (813, 353, 931, 416), "EF06": (813, 438, 930, 500), "EF08": (813, 534, 930, 597), "EF09": (813, 607, 931, 671),
    "EF10": (813, 679, 931, 741), "EF11": (813, 763, 931, 826), "EF12": (812, 837, 931, 899), "EF13": (812, 908, 931, 969),
    "EF14": (810, 981, 933, 1042), "EF15": (812, 1088, 934, 1149), "EF16": (811, 1158, 934, 1218), "EF17": (811, 1229, 931, 1289),
    "EF18": (811, 1300, 931, 1360),
    "ERLY01": (942, 49, 1046, 160), "ERLY02": (942, 198, 1047, 302), "ERLY03": (956, 342, 1046, 443), "EF25": (939, 474, 1046, 536),
    "EF07": (939, 563, 1046, 625), "ERLY04": (955, 662, 1047, 749), "ERLY06": (942, 833, 1046, 943), "ERLY07": (942, 993, 1047, 1114),
    "SB01": (995, 1157, 1047, 1275), "ERLY08": (942, 1325, 1046, 1429),
}
_S800_ENGINE_MARKS: List[Tuple[str, Tuple[int, int, int, int]]] = [
    ("B+", (227, 920, 295, 988)), ("", (460, 629, 552, 700)),                       # the battery stud and the fuse puller
    ("", (517, 186, 571, 280)), ("", (618, 1090, 674, 1183)), ("", (618, 1196, 674, 1290)), ("", (942, 1157, 987, 1275)), ("", (42, 1369, 193, 1434)),
]

_S800_CABIN: List[Row] = [
    ("RF01", 10, _t("Pantalla y sonido (IHU) y módulo de carrocería (BDM 4)", "Head unit (IHU) and body control module (BDM 4)", "Unidade de áudio (IHU) e módulo de carroçaria (BDM 4)"), None),
    ("RF02", 10, _t("Módulo USB, vigilancia del conductor (DMS) y conectores RPST y QPST", "USB module, driver monitoring (DMS) and RPST and QPST connectors",
                    "Módulo USB, vigilância do condutor (DMS) e conectores RPST e QPST"), None),
    ("RF03", 10, _t("Dirección asistida eléctrica (EPS)", "Electric power steering (EPS)", "Direção assistida elétrica (EPS)"), None),
    ("RF04", None, _t("Pantalla y sonido (IHU)", "Head unit (IHU)", "Unidade de áudio (IHU)"),
     _t("La tabla del manual dice 15 A y su dibujo 20 A. Mira el fusible que lleva puesto.", "The manual's table says 15 A and its drawing 20 A. Look at the fuse that is fitted.",
        "A tabela do manual diz 15 A e o desenho 20 A. Vê o fusível que está montado.")),
    ("RF05", 10, _t("Centralita de los airbags", "Airbag control unit", "Unidade de controlo dos airbags"), None),
    ("RF06", 10, _t("Climatización, llamada de emergencia, mandos del volante, head-up display, consola auxiliar, cuadro, amplificador, luz ambiental del techo, panel de clima trasero y módulo Bluetooth de la puerta trasera",
                    "Climate control, emergency call, steering wheel controls, head-up display, auxiliary console, instrument panel, amplifier, sunroof ambient light, rear climate panel and rear door Bluetooth module",
                    "Climatização, chamada de emergência, comandos do volante, head-up display, consola auxiliar, painel de instrumentos, amplificador, luz ambiente do teto, painel de clima traseiro e módulo Bluetooth da porta traseira"), None),
    ("RF07", 10, _t("Head-up display, palanca de cambios electrónica, llamada de emergencia, climatización, luz de cortesía delantera y mandos del volante",
                    "Head-up display, electronic gear selector, emergency call, climate control, front courtesy light and steering column switches",
                    "Head-up display, seletor de mudanças eletrónico, chamada de emergência, climatização, luz de cortesia dianteira e comandos do volante"), None),
    ("RF08", 10, _t("Cámara de a bordo, sensor de calidad del aire, aparcamiento asistido, telepeaje, luz ambiental de las puertas y sensor de lluvia y luz",
                    "Dash camera, air quality sensor, parking assist, toll tag, door ambient lighting and rain and light sensor",
                    "Câmara de bordo, sensor de qualidade do ar, estacionamento assistido, portagem eletrónica, luz ambiente das portas e sensor de chuva e luz"), None),
    ("RF09", 15, UNCONFIRMED, _S800_NO_FUNCTION), ("RF10", 10, UNCONFIRMED, _S800_NO_FUNCTION),
    ("RF11", 30, _t("Módulo de la puerta (DCM 2) y enganche de remolque", "Door module (DCM 2) and towing hook", "Módulo da porta (DCM 2) e engate de reboque"), None),
    ("RF12", 20, _t("Amplificador de sonido", "Audio amplifier", "Amplificador de som"), None),
    ("RF13", 25, UNCONFIRMED, _S800_NO_FUNCTION),
    ("RF14", 30, _t("Módulo de carrocería (BDM)", "Body control module (BDM)", "Módulo de carroçaria (BDM)"),
     _t("La tabla del manual dice «BDM 6»; su dibujo, «BDM 1».", "The manual's table says «BDM 6»; its drawing, «BDM 1».", "A tabela do manual diz «BDM 6»; o desenho, «BDM 1».")),
    ("RF15", 7.5, _t("Toma de diagnosis (OBD), cable espiral del volante, regulador de la iluminación y panel de la consola auxiliar",
                     "Diagnostic socket (OBD), steering wheel clock spring, lighting adjuster and auxiliary console panel",
                     "Tomada de diagnóstico (OBD), cabo espiral do volante, regulador da iluminação e painel da consola auxiliar"), None),
    ("RF16", 7.5, _t("Módulo EMI (base de conexión)", "EMI module (docking station)", "Módulo EMI (base de ligação)"), None),
    ("RF17", None, None, None),
    ("RF18", 10, _t("Retrovisores exteriores", "Door mirrors", "Retrovisores exteriores"), None),
    ("RF19", 10, _t("Climatización, panel de clima trasero, vigilancia del conductor y conector del asiento del acompañante",
                    "Climate control, rear climate panel, driver monitoring and front passenger seat connector",
                    "Climatização, painel de clima traseiro, vigilância do condutor e conector do banco do passageiro"), None),
    ("RF20", 40, _t("Ventilador del habitáculo", "Cabin blower", "Ventilador do habitáculo"), None),
    ("RF21", 7.5, _t("Cambio electrónico (EGS) y detección de niños en el coche", "Electronic gear shift (EGS) and child presence detection",
                     "Mudanças eletrónicas (EGS) e deteção de crianças no carro"), None),
    ("RF22", 20, _t("Módulo de carrocería (BDM)", "Body control module (BDM)", "Módulo de carroçaria (BDM)"),
     _t("La tabla del manual dice «BDM 3»; su dibujo, «BDM5».", "The manual's table says «BDM 3»; its drawing, «BDM5».", "A tabela do manual diz «BDM 3»; o desenho, «BDM5».")),
    ("RF23", 15, _t("Volante calefactable", "Heated steering wheel", "Volante aquecido"), None),
    ("RF24", 30, _t("Luneta térmica", "Rear window demister", "Desembaciador do vidro traseiro"), None),
    ("RF25", 15, _t("Carga inalámbrica del móvil y bloqueo por alcoholímetro", "Wireless phone charger and alcohol interlock", "Carregador sem fios do telemóvel e bloqueio por alcoolímetro"), None),
    ("RF26", 10, _t("Carga inalámbrica del móvil, pantalla y sonido (IHU) y módulo de carrocería (BDM 4)", "Wireless phone charger, head unit (IHU) and body control module (BDM 4)",
                    "Carregador sem fios do telemóvel, unidade de áudio (IHU) e módulo de carroçaria (BDM 4)"), None),
    ("RF27", 15, _t("Bloqueo por alcoholímetro", "Alcohol interlock", "Bloqueio por alcoolímetro"), None),
    ("RF28", 10, _t("Módulo de carga USB y toma de la cámara de a bordo", "USB charging module and dash camera socket", "Módulo de carregamento USB e tomada da câmara de bordo"), None),
]
_S800_CABIN_RELAYS: List[Tuple[str, Text]] = [
    ("RRLY01", _t("Ventilador del habitáculo", "Cabin blower", "Ventilador do habitáculo")),
    ("RRLY02", _t("Volante calefactable", "Heated steering wheel", "Volante aquecido")),
    ("RRLY03", _t("Luneta térmica", "Rear window demister", "Desembaciador do vidro traseiro")),
]
_S800_CABIN_FRAME = (160, 42, 1048, 668)
_S800_CABIN_LAYOUT: Dict[str, Tuple[int, int, int, int]] = {
    "RRLY01": (174, 56, 416, 296), "RF01": (424, 53, 578, 117), "RF02": (586, 53, 740, 117), "RF03": (424, 125, 578, 189), "RF04": (586, 125, 740, 189),
    "RF05": (424, 197, 578, 261), "RF06": (586, 197, 740, 261), "RF07": (424, 269, 578, 333), "RF08": (586, 269, 740, 333),
    "RRLY02": (748, 110, 887, 311), "RRLY03": (896, 110, 1034, 311), "RF09": (174, 305, 238, 459), "RF10": (249, 305, 313, 459),
    "RF11": (334, 373, 445, 498), "RF20": (334, 531, 445, 656),
    "RF12": (454, 341, 517, 494), "RF13": (526, 341, 589, 494), "RF14": (597, 341, 661, 495), "RF15": (669, 341, 733, 495),
    "RF16": (755, 341, 819, 495), "RF17": (827, 341, 891, 494), "RF18": (899, 341, 962, 495), "RF19": (971, 341, 1034, 494),
    "RF21": (454, 503, 517, 656), "RF22": (526, 503, 589, 656), "RF23": (597, 503, 661, 657), "RF24": (669, 503, 733, 657),
    "RF25": (755, 503, 819, 657), "RF26": (827, 503, 891, 657), "RF27": (899, 503, 962, 657), "RF28": (971, 503, 1034, 656),
}
_S800_CABIN_MARKS: List[Tuple[str, Tuple[int, int, int, int]]] = [("B+", (201, 499, 282, 580))]

# model → what is known. `where` comes from Ebro's own owner's manual (it does say where the boxes are).
BOXES: Dict[str, Dict[str, Any]] = {
    "S900 PHEV": {
        "source": ("Manual de taller del Chery Fengyun T9 (plataforma T28 PHEV, 2024), el coche sobre el que está hecho el S900. Mercado chino.",
                   "Workshop manual of the Chery Fengyun T9 (T28 PHEV platform, 2024), the car the S900 is built on. Chinese market.",
                   "Manual de oficina do Chery Fengyun T9 (plataforma T28 PHEV, 2024), o carro em que o S900 se baseia. Mercado chinês."),
        "verified": False,
        "boxes": [
            {"key": "cabin", "name": ("Habitáculo", "Cabin", "Habitáculo"),
             "where": ("Bajo el salpicadero, a la izquierda, detrás del protector inferior.", "Under the dashboard, on the left, behind the lower trim panel.",
                       "Sob o tablier, à esquerda, atrás do painel inferior."),
             "fuses": _CABIN, "relays": _CABIN_RELAYS,
             "layout": {"frame": _CABIN_FRAME, "cells": _CABIN_LAYOUT, "marks": _CABIN_MARKS, "view": _CABIN_VIEW}},
            {"key": "engine", "name": ("Vano motor", "Engine bay", "Compartimento do motor"),
             "where": ("En el vano motor, lado izquierdo del coche, bajo su tapa. La batería de 12 V está en el maletero, a la derecha.",
                       "In the engine bay, on the car's left side, under its cover. The 12 V battery is in the boot, on the right.",
                       "No compartimento do motor, lado esquerdo do carro, sob a tampa. A bateria de 12 V está na bagageira, à direita."),
             "fuses": _ENGINE, "relays": _ENGINE_RELAYS,
             "layout": {"frame": _ENGINE_FRAME, "cells": _ENGINE_LAYOUT, "marks": _ENGINE_MARKS}},
        ],
    },
    "S800 PHEV": {
        "source": ("Manual del propietario del CAOA Chery Tiggo 8 Pro híbrido enchufable (Brasil, edición 2026/27), el mismo coche que el S800: mismo motor, "
                   "mismo cambio y las mismas ilustraciones que el manual de Ebro. Ese manual solo habla de la tracción delantera: en un S800 con tracción "
                   "total puede haber posiciones distintas.",
                   "Owner's manual of the CAOA Chery Tiggo 8 Pro plug-in hybrid (Brazil, 2026/27 edition), the same car as the S800: same engine, same "
                   "gearbox and the same illustrations as Ebro's manual. That manual covers front-wheel drive only: an all-wheel-drive S800 may have "
                   "different positions.",
                   "Manual do proprietário do CAOA Chery Tiggo 8 Pro híbrido plug-in (Brasil, edição 2026/27), o mesmo carro que o S800: mesmo motor, "
                   "mesma caixa e as mesmas ilustrações que o manual da Ebro. Esse manual só fala da tração dianteira: num S800 com tração integral "
                   "pode haver posições diferentes."),
        "verified": False,
        "boxes": [
            {"key": "cabin", "name": ("Habitáculo", "Cabin", "Habitáculo"),
             "where": ("Detrás del protector inferior izquierdo del salpicadero.", "Behind the lower left dashboard panel.", "Atrás do painel inferior esquerdo do tablier."),
             "fuses": _S800_CABIN, "relays": _S800_CABIN_RELAYS,
             "layout": {"frame": _S800_CABIN_FRAME, "cells": _S800_CABIN_LAYOUT, "marks": _S800_CABIN_MARKS}},
            {"key": "engine", "name": ("Vano motor", "Engine bay", "Compartimento do motor"),
             "where": ("En el lado izquierdo del vano motor, bajo su tapa. Hay una tercera caja con tres fusibles de alta intensidad (BF01 a BF03) en el "
                       "borne de la batería de 12 V, detrás del guarnecido izquierdo del maletero; el manual no dice qué protegen.",
                       "On the left side of the engine bay, under its cover. There is a third box with three high-current fuses (BF01 to BF03) on the "
                       "12 V battery terminal, behind the left boot trim; the manual does not say what they protect.",
                       "No lado esquerdo do compartimento do motor, sob a tampa. Há uma terceira caixa com três fusíveis de alta intensidade (BF01 a BF03) "
                       "no borne da bateria de 12 V, atrás do revestimento esquerdo da bagageira; o manual não diz o que protegem."),
             "fuses": _S800_ENGINE, "relays": _S800_ENGINE_RELAYS,
             "layout": {"frame": _S800_ENGINE_FRAME, "cells": _S800_ENGINE_LAYOUT, "marks": _S800_ENGINE_MARKS}},
        ],
    },
}


def _layout(layout: Optional[Dict[str, Any]], lang_index: int) -> Optional[Dict[str, Any]]:
    """The place of each fuse as the page draws it: left, top, width and height in % of the box, whose proportions are `ratio`
    (width ÷ height). None for a box whose arrangement is not known (its fuses are then listed by number)."""
    if not layout:
        return None
    x0, y0, x1, y1 = layout["frame"]
    w, h = x1 - x0, y1 - y0

    def pct(rect: Tuple[int, int, int, int]) -> List[float]:
        a, b, c, d = rect
        return [round((a - x0) / w * 100, 2), round((b - y0) / h * 100, 2), round((c - a) / w * 100, 2), round((d - b) / h * 100, 2)]

    view = layout.get("view")                    # a line that helps to find one's way on the real box, where there is one worth saying
    return {"ratio": round(w / h, 4), "view": view[lang_index] if view else "", "cells": {ident: pct(rect) for ident, rect in layout["cells"].items()},
            "marks": [{"label": label, "at": pct(rect)} for label, rect in layout["marks"]]}


def has(model: Optional[str]) -> bool:
    return normalize_model(model) in BOXES


def for_model(model: Optional[str], lang: str = "es") -> Optional[Dict[str, Any]]:
    """The fuse boxes of a model in one language, or None when nothing is known about it. Each fuse: `id`, `amps` (None = the
    rating is in doubt or the position is empty), `kind` (`fuse`, `spare`, `empty` or `relay`), `text` and, where the source
    contradicts itself or a real car was seen to differ, `doubt`."""
    key = normalize_model(model)
    data = BOXES.get(key or "")
    if data is None:
        return None
    i = LANGS.index(lang) if lang in LANGS else 0
    boxes = []
    for box in data["boxes"]:
        items: List[Dict[str, Any]] = []
        for ident, amps, text, doubt in box["fuses"]:
            kind = "fuse" if text is not None else ("spare" if amps is not None else "empty")
            items.append({"id": ident, "amps": amps, "kind": kind, "text": (text or SPARE)[i] if kind != "empty" else "", "doubt": doubt[i] if doubt else None})
        for ident, text in box["relays"]:
            items.append({"id": ident, "amps": None, "kind": "relay", "text": text[i], "doubt": None})
        boxes.append({"key": box["key"], "name": box["name"][i], "where": box["where"][i], "items": items, "layout": _layout(box.get("layout"), i)})
    return {"model": key, "source": data["source"][i], "verified": data["verified"], "boxes": boxes}
