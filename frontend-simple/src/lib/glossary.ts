// Términos del rubro que un analista espera ver. Se mantienen, con una explicación de una línea al alcance.
export const GLOSSARY: Record<string, string> = {
  tlp: "Protocolo de semáforo (TLP 2.0): indica quién puede recibir el caso o su contenido.",
  ioc: "Indicador de compromiso: un dato técnico (dominio, IP, hash, URL) que sirve para detectar actividad maliciosa.",
  stix: "STIX 2.1: formato estándar para intercambiar inteligencia de amenazas entre organizaciones.",
  misp: "MISP: plataforma de intercambio de indicadores. El archivo se puede importar allí.",
  attack: "MITRE ATT&CK: catálogo público de técnicas que usan los atacantes, con su código (por ejemplo T1566).",
  menard: "MENARD compara estilo, horarios y red de cuentas para sugerir si pertenecen al mismo operador.",
  funes: "FUNES es la IA del sistema. Corre en la red propia y propone datos; nunca decide.",
  hipotesis: "Una hipótesis es una sugerencia con evidencia. Confirmarla no prueba nada por sí sola.",
  confianza: "Qué tan sólido es el conjunto de indicios. No es una probabilidad ni una certeza.",
  admiralty: "Fiabilidad de la fuente (A a F) y credibilidad del dato (1 a 6), según la escala Admiralty.",
  baselegal: "La autorización con la que se investiga: una orden, una denuncia o un encargo. Sin ella el caso no se abre.",
  propuesta: "Dato que el sistema o una herramienta sugirió. No cuenta como confirmado hasta que alguien lo revisa.",
  expediente: "La lista ordenada por incisos de lo que el caso ya tiene, con el origen de cada dato.",
  cadena: "Cada evento de auditoría incluye el hash del anterior. Si alguien altera uno, la cadena se rompe y se detecta.",
  grafo: "Mapa de conexiones: cada punto es una entidad y cada línea una relación entre dos de ellas.",
  datachunk: "Fragmento detectado en un texto (un dominio, un correo, una fecha) con la cita exacta de donde salió.",
};

export const RELIABILITY_HELP: Record<string, string> = {
  A: "Completamente fiable",
  B: "Normalmente fiable",
  C: "Bastante fiable",
  D: "Normalmente no fiable",
  E: "No fiable",
  F: "No se puede juzgar",
};

export const CREDIBILITY_HELP: Record<string, string> = {
  "1": "Confirmado por otras fuentes",
  "2": "Probablemente cierto",
  "3": "Posiblemente cierto",
  "4": "Dudoso",
  "5": "Improbable",
  "6": "No se puede juzgar",
};
