# Manual de EVBro: todas las pantallas y lo que hace cada una

*English version: [docs/en/manual.md](en/manual.md).*

EVBro es el compañero de tu coche Ebro: ve lo que el coche cuenta a la nube de Chery, le manda órdenes, guarda su historia (viajes, cargas, repostajes, talleres, gastos) y te avisa de lo que importa. Está pensado para el móvil (dock inferior con **Inicio, Carga, Confort y Más**) y en el ordenador se convierte en una barra lateral con todas las secciones a la vista. Disponible en español, inglés y portugués (Ajustes → Sistema → Idioma).

Una regla que vale para toda la app: **lo que ves sale del coche**. Si un dato no ha llegado, verás «Sin datos», nunca un valor inventado ni un «cerrado» por defecto.

## Inicio

La portada del coche elegido (arriba, el selector de coche si tienes varios).

- **Cabecera con la foto** del coche, su nombre, matrícula y estado: *Dormido*, *En marcha*, *Cargando*… El punto de color indica si el dato es reciente.
- **Batería y gasolina**: porcentaje y autonomía de cada una (en un eléctrico puro no hay gasolina; en uno de combustión no hay batería).
- **Figura del coche** vista desde arriba: puertas y portón abiertos giran hacia fuera, ventanillas y techo abiertos se pintan en ámbar, el candado dice si está cerrado, el enchufe aparece con el cable conectado y cada rueda lleva su presión. Debajo, una línea resume lo abierto. **Tocar una parte abre su acción** (ventanillas, maletero, techo, cierre).
- **Fichas de acción**: Cierre, Buscar (luces y claxon), Maletero, Ventanillas, Techo, Localizar GPS y Alarma. El título dice qué es y el estado solo *Abierto/Cerrado*. Lo que **abre** el coche pide el PIN de Ebro en cada envío (es configurable, ver Ajustes → Cuenta).
- **Mapa**: la última posición conocida. Plegado es una vista fija; un toque lo amplía a pantalla completa. **Centrar** vuelve al coche e **Ir a** abre Google Maps hasta él. La posición viene de la nube (no despierta el coche); **Localizar GPS** sí pide al coche una posición nueva.
- **Neumáticos (TPMS)**: presión y temperatura de cada rueda.
- Botón **Actualizar** (arriba a la derecha): consulta la nube ahora. No despierta el coche.

**Personalizar inicio** (botón al final de la página). Cada bloque es un *widget* que puedes **mover** (arrastrando con el asa ⠿ o con las flechas), **quitar** (×) o **volver a poner** con «Añadir widget». La cabecera se queda siempre arriba. Además de los bloques de siempre hay widgets nuevos: **Próximo mantenimiento** (la revisión o tarea más cercana y la ITV), **Último viaje**, **Última carga**, **Gasto del mes**, **Carga programada** (objetivo de batería y horario), **Climatización** (temperaturas y encender o apagar) y **Consumo** de los últimos 30 días. Tocar uno abre su sección. Mientras editas, los botones no hacen nada, para no mandar una orden sin querer. La disposición es **de tu cuenta**: se ve igual en el móvil y en el ordenador, y cada persona con acceso tiene la suya. «Restablecer» vuelve al inicio de siempre. Los widgets que tu coche no puede mostrar (datos en directo sin cuenta de Ebro, cargas en uno de gasolina) no aparecen. En el ordenador el mapa va siempre en su columna de la derecha.

**Carga** y **Confort** se personalizan igual, con «Personalizar carga» y «Personalizar confort» al final de cada página: puedes ordenar o quitar sus bloques (batería y objetivo, cifras, control de carga, historial de cargas, carga programada, detalle de la carga; climatización, lunas y volante, asientos, climatización programada) y, en Carga, añadir la última carga, el gasto del mes o el consumo. Cada página guarda su orden y «Restablecer» solo afecta a esa página.

**Atajos del icono.** Con EVBro instalada en el móvil, mantén pulsado su icono: salen **Cerrar el coche**, **Encender el clima**, **Carga** y **Viajes** (cada uno se puede arrastrar al escritorio como un icono propio). Los de órdenes no mandan nada solos: abren la app y te preguntan antes, con el nombre del coche. EVBro no puede poner un widget con datos en el escritorio de Android, porque Chrome no lo permite a las aplicaciones web.

**Barra inferior.** En el móvil puedes elegir qué va en la barra de abajo: Inicio y Más siempre en los extremos y hasta 4 secciones en medio (6 iconos como máximo), entre Carga, Confort y cualquiera de las de Más. Se cambia con **Personalizar barra inferior**, al final de Más: añadir, quitar y ordenar con flechas. Se guarda en tu cuenta, así que sale igual en todos tus móviles; «Restablecer» vuelve a Carga y Confort. Lo que pones en la barra deja de salir en Más, para no tenerlo dos veces; si lo quitas de la barra, vuelve a Más. En el ordenador no hay barra inferior (todo está en la barra lateral).

## Carga

*(Se llamaba «Energía» hasta el 01-11-2026.)*

Todo lo de la batería y la carga.

- **Carga actual** y **objetivo de batería** (50–100 %, de 5 en 5, con el deslizador). El objetivo se guarda en EVBro, no en el coche: cuando cargando se alcanza, EVBro **pausa** la carga, y la reanuda sola al enchufar con la batería 5 puntos por debajo.
- **Control de carga: Parar / Iniciar**. El coche no admite parar o iniciar la carga directamente; EVBro lo consigue con la **carga programada del propio coche** (programa una ventana que ya terminó para pararla y la quita para iniciarla). Hay un desplegable «Cómo funciona» que lo explica en la propia pantalla.
- **Carga programada**: hora de inicio y fin (o duración) para cargar todos los días en esa franja (tarifa valle). Se escribe en el coche.
- **Consumo EV · 30 días**: kWh/100 km de los viajes del último mes. Cuenta solo los viajes de 2 km o más con energía medida, la misma regla que el resumen de Viajes y las Estadísticas: en un trayecto muy corto, un solo punto de batería y el kilómetro redondeado del odómetro dan una cifra sin sentido.
- **Historial de cargas**: cada sesión con lugar, kWh a la batería (estimados por los puntos de batería ganados) y su coste. Tocar una abre su **detalle**.
- **Detalle de la carga**: la curva de potencia minuto a minuto, el mapa, la energía y el coste de la carga elegida (la última, si no eliges otra). Con **Indicar kWh del contador** escribes los kWh que marcó tu contador o cargador y EVBro calcula la **eficiencia** real. El coste sale de la **tarifa de la geocerca** donde cargaste (fija, gratuita, 2.0TD o PVPC) o lo escribes con **Editar coste**. Se puede marcar una carga como AC o DC rápida y ver la curva de potencia minuto a minuto.

Nota honesta: el control de carga por programación está construido siguiendo el protocolo de la app oficial, pero **no se ha podido validar en todas sus situaciones contra el coche real**. Si algo no cuadra, avísalo.

## Confort

Lo que se puede encender desde el sofá.

- **Climatizador**: encender/apagar con temperatura de consigna (16–30 °C) y preajustes *Fresco / 22 °C / Cálido*. Al encenderlo se manda la temperatura elegida.
- **Lunas y volante**: desempañado de parabrisas y luneta, volante calefactado.
- **Asientos**: calefacción y ventilación por plaza.
- **Climatización programada**: el servidor enciende el climatizador a la hora y los días que elijas, aunque la app esté cerrada. Necesita el PIN guardado en la cuenta.
- Cada botón muestra el estado real del coche; tras una orden aparece «esperando confirmación» hasta que el coche la confirma.

## Más

El menú con el resto de secciones, agrupadas en **Historial** y **Gestión**.

### Historial

**Estadísticas.** Gráficas del historial con selector de periodo (12 meses, este año, el anterior, todo…): kilómetros por mes, energía por mes (kWh y litros), gasto por mes, coste por kilómetro, consumo eléctrico por mes, salud de la batería (capacidad útil **medida** con la curva de potencia de cada carga de 20 puntos o más, y cuánto es frente a la capacidad de la ficha), consumo según la velocidad, dónde se carga y gasto en reposo (batería que pierde el coche aparcado).

**Cronología.** Todo lo apuntado del coche en una sola lista por fecha (viajes, cargas, repostajes, taller, gastos, documentos, entradas y salidas de geocercas), con filtros por tipo. Arriba, el **buscador global**: una referencia de pieza, un taller, una factura o una dirección, en todos tus coches. Tocar un elemento te lleva a su pantalla.

**Viajes.** Cada trayecto detectado con origen y destino (dirección), kilómetros del odómetro, duración, batería y gasolina gastadas y velocidades. **Ver trazado** abre el mapa con la ruta, se puede **reproducir** y descargar en **GPX**. Filtros por geocerca, lugar, distancia mínima, velocidad y periodo. El **resumen del periodo** suma todos los viajes completos; su consumo medio en kWh/100 km se calcula solo con los viajes de 2 km o más con energía medida, igual que en Carga y Estadísticas, así que con el mismo periodo sale la misma cifra. **Lugares frecuentes**: dónde termina más veces el coche; desde ahí se crea una geocerca. **Viajes largos**: agrupa, entre dos fechas, los trayectos, cargas, repostajes y gastos de unas vacaciones y te dice cuánto costó y consumió todo. Los viajes se detectan solos a partir de los datos del coche; los incompletos (sin fin) se pueden corregir a mano.

**Repostajes.** Consumo real de gasolina: litros entre llenados, coste por 100 km, precio del litro, autonomía con el depósito lleno. Los repostajes se detectan solos cuando sube el depósito (con litros estimados); añade los que falten y pon el precio del ticket. Marca **Depósito lleno** cuando llenes: entre dos llenados el consumo es exacto.

**Mantenimiento.** Dos planes: el **oficial de EBRO** (la pauta de la marca: 15.000 a 90.000 km, con lo que se reemplaza y se inspecciona en cada revisión y el tiempo oficial de taller) o uno **personalizado** con tus tareas e intervalos en kilómetros o meses. La **ITV** se calcula desde la matriculación (o desde la pegatina que indiques). Registra cada **revisión**, **intervención puntual** (pastillas, batería de 12 V…) o ITV con fecha, kilómetros, taller, coste, notas, las **piezas montadas** y la **factura** en PDF o foto. Los avisos de mantenimiento salen por los canales de Ajustes → Avisos.

**Gastos.** Seguro, impuestos, garaje, peajes, multas y otros pagos, con categoría. Para los que se repiten (seguro, impuesto) se indica la **próxima vez** y EVBro avisa 30 días antes.

**Costes.** El balance de todo: compra, cargas, repostajes, mantenimiento y gastos; total desde la compra, gasto de uso, coste por 100 km y por categoría, con gráfica mensual y selector de periodo.

### Gestión

**Coches.** La lista de tus coches (toca uno para que toda la app pase a él) y la **ficha** del elegido:
- *Datos*: nombre, marca, modelo, versión, color, etiqueta ambiental, matrícula, bastidor, fechas de matriculación y entrega, precio y kilómetros de compra, **seguro** (compañía, póliza, cobertura, teléfono de asistencia, renovación), **documentación** (permiso de circulación, ficha técnica, póliza, factura de compra…: se suben y se abren desde la app), **neumáticos** (medidas y presiones de referencia para el aviso) y **energía** (batería útil, depósito y tipo de motor; si cambias la batería se recalculan todos los viajes y cargas).
- *Consumibles*: lo que lleva tu coche (aceite, filtros, pastillas, neumáticos, escobillas…), con marca, **Ref. marca** (el número que pone la marca de la pieza: Mann, Bosch…) y **Ref. fabricante** (el número de pieza de la marca del coche: el de Ebro en un Ebro), para saberlo a la primera en el taller. Se actualiza solo al registrar una intervención con piezas. Puedes **proponer** una pieza al catálogo compartido, también al añadirla («Guardar y proponer»); el administrador la revisa antes de publicarla para los demás dueños del mismo modelo. Con **Ver catálogo online** la lista enseña, junto a tus piezas, las aprobadas del catálogo para tu modelo; cada pieza tuya dice en qué estado está (*sin proponer*, *propuesta enviada*, *en el catálogo* o *rechazada*). Tocar una del catálogo la copia a tu coche, y si ya tenías una de ese tipo te avisa de que la sustituye.
- **Libro del coche (PDF)**: un documento con las secciones que elijas (ficha, mantenimiento, viajes, cargas, repostajes, gastos, geocercas, documentos) para guardar, imprimir o entregar con el coche. Sale en tu idioma.
- **Añadir coche sin cuenta Ebro**: un coche de cualquier marca para llevar su mantenimiento, gastos, costes y piezas (sin datos en directo). Con un localizador GPS y un servidor Traccar también tiene posición y viajes.
- **Foto** del coche, que se usa en la cabecera de Inicio.

**Geocercas.** Zonas en el mapa (casa, trabajo, cargador…) con radio. Sirven para tres cosas: **avisar** al entrar o salir, **poner precio** a las cargas hechas dentro (tarifa fija, gratis, 2.0TD con sus tres precios o PVPC regulado) y clasificar los viajes. En la 2.0TD los tramos siguen la hora de España (la de Canarias en su zona); los sábados, los domingos y los festivos nacionales de fecha fija (1 y 6 de enero, 1 de mayo, 15 de agosto, 12 de octubre, 1 de noviembre y 6, 8 y 25 de diciembre) son valle todo el día. Los festivos autonómicos, los trasladados a lunes y el Viernes Santo no cuentan, como marca la Circular 3/2020 de la CNMC. Si creas una geocerca en un sitio donde ya habías estado, las cargas y viajes anteriores de ese sitio pasan a llevar su nombre; **sus precios no cambian** (solo las cargas nuevas toman la tarifa). La tarjeta **Entradas y salidas** es el registro de cada paso por ellas.

**Ajustes.** Cada apartado es una sección desplegable; el móvil recuerda cuáles dejaste abiertas.
- *Cuenta*: tu usuario de EVBro (cambiar contraseña, cerrar sesión), **acceso con huella** (añade este dispositivo y entra sin contraseña; exige HTTPS), la **cuenta de Ebro** (conectar, sincronizar coches, guardar o cambiar el PIN) y **cuándo pedir el PIN**: solo al abrir el coche (por defecto), siempre o nunca. **Tus datos**: descargar todo en un ZIP e importar un ZIP en otro servidor. Enlace a qué datos guarda este servidor.
- *Compartir*: cuentas asociadas (pareja, familia) y qué puede hacer cada una con cada coche: ver; ver y editar; ver y controlar; ver, editar y controlar.
- *Avisos*: notificaciones en este dispositivo (Web Push) o Telegram, y qué eventos avisan: entrada y salida de geocercas, reserva de combustible, fallos, ventanas abiertas, batería baja, carga completada o interrumpida, mantenimiento, alarma, presión de neumáticos y vencimientos, con sus umbrales. Historial de avisos enviados.
- *Conexiones*: el **canal del coche** (la conexión en directo con la nube: estado, mensajes recibidos, botón Reconectar), el broker **MQTT de Home Assistant**, para que tus coches aparezcan allí, y la posición por GPS desde un servidor **Traccar**.
- *Sistema*: **apariencia** (tema claro, oscuro o automático y color de acento: azul, rosa, violeta, verde, ámbar o rojo; se guarda en el dispositivo), **idioma** de la cuenta y versión instalada.

**Sensores y cables.** Todos los datos que envía el coche tal cual llegan, con su nombre y explicación, por categorías (cables y carga, batería, neumáticos, carrocería, climatización, motor y diagnóstico) y un buscador. Útil para entender qué significa cada señal.

**Centro de avisos** (campana de la cabecera): los últimos eventos y avisos del coche.

## Cómo llegan los datos

- La **sonda** consulta la nube de Chery cada hora con el coche aparcado, cada 30 minutos enchufado, cada 2 cargando y cada minuto en marcha. Consultar **no despierta el coche**; los **comandos** sí.
- El **canal del coche** (MQTT) recibe al instante puertas, cierre, cable y motor, y confirma cada orden. Solo funciona con la cuenta del propietario, y la nube mantiene **una sola conexión en directo por cuenta**: si abres la app oficial, la última que entra se queda con ella. Si el canal se para, Ajustes → Conexiones → Reconectar.
- Los viajes y las cargas se **detectan solos** a partir de esos datos; los kWh de viajes y cargas son estimaciones (puntos de batería × capacidad de la ficha). Los litros de un repostaje detectado también, hasta que pones los del ticket.

## La consola de administración

Es otra web, en otro puerto, solo para la red local. El administrador no tiene coches ni ve los datos de nadie: crea **gestores**, emite **invitaciones**, pone **límites** (coches, cuentas asociadas, espacio para ficheros), ajusta el **servidor** (cuánto historial se conserva, intervalos de sondeo), modera el **catálogo compartido** de consumibles (aprobar, rechazar, editar propuestas, añadir piezas ya aprobadas; conectar con el catálogo de otro servidor) y consulta un **registro** de sus acciones y cifras agregadas del servidor. También está en los tres idiomas.

**Informes anónimos.** Al crear el administrador (o, si no se contestó, la primera vez que se entra) la consola pregunta si quieres enviar al proyecto un informe anónimo al día: versión, qué funciones se usan, cómo responden la nube de Ebro y las órdenes, y los fallos registrados. Ayuda a encontrar y corregir errores. No lleva nombres, usuarios, matrículas, VIN, posiciones, viajes ni importes, y con **Ver qué se envía** lees el informe exacto antes de decidir. Se activa o desactiva cuando quieras en la pestaña Servidor.

**Catálogo del proyecto.** Al crear el administrador eliges si usas el catálogo compartido de consumibles del proyecto o solo el de tu servidor (si no se contestó, la consola lo pregunta arriba). Para conectarlo después basta con **Conectar con el catálogo del proyecto** en la pestaña Catálogo: el servidor se da de alta solo en `catalog.evbro.app`, sin pedir ninguna clave. Con una clave que te hayan dado (`ebc_…`) también vale pegarla. Se puede volver al catálogo propio cuando quieras.

## Lo que EVBro no hace (a propósito)

- No arranca el motor a distancia (el coche lo rechaza) ni para o inicia la carga «de verdad»: lo hace con la programación.
- No guarda tu PIN en el navegador; en el servidor va cifrado y solo si tú quieres.
- No envía tus datos a nadie: el servidor es tuyo. Las únicas salidas son la nube de Chery (para hablar con el coche), los mapas de OpenStreetMap, los canales de aviso que tú actives y, solo si el administrador lo activa, un informe anónimo al día para el proyecto (sin datos personales).
