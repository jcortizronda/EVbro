"""Núcleo de protocolo Ebro/Chery de EVBro.

Contiene la lógica verificada sobre el terreno: autenticación BFF, firma de las peticiones,
catálogo y envío de comandos, sonda realtime, despertar. Procede de la integración
la integración de Home Assistant para estos coches y se mantiene lo más parecido
posible a ella para poder sincronizar correcciones en ambos sentidos. Ni la API ni la interfaz
hablan nunca directamente con la nube: pasan todas por aquí.

Los módulos de esta carpeta se importan entre sí con imports relativos de paquete
(`from .core import …` / `from . import …`), así es Python quien garantiza qué módulo se
carga y los loggers quedan atribuidos a EVBro.
"""
