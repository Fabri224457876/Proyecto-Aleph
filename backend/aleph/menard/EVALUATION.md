# MENARD — evaluación

Resultados reales del evaluador, sin retoques. Se regeneran con:

```
cd backend
../.venv/Scripts/python -m aleph.menard.eval --train --write-md   # reentrena y evalúa
../.venv/Scripts/python -m aleph.menard.eval --write-md           # solo evalúa
```

## Qué se mide y qué no

- Dataset **sintético** (`aleph.menard.synth`): 4 mundos de prueba (semillas [201, 202, 203, 204]), 150 personas ficticias cada uno, 990 cuentas elegibles en total. Los pares solo se forman dentro de un mundo.
- La fusión se entrenó con otros mundos (semillas [101, 102, 103, 104, 105]): ninguna persona de prueba aparece en entrenamiento. Entrenamiento y prueba sí comparten el *proceso generador* (mismas plantillas y vocabulario), así que estos números son un techo optimista.
- **No hay todavía evaluación con datos reales.** El texto sintético sale de plantillas y es mucho más regular que el de personas reales; los pesos y la calibración hay que revalidarlos con datos etiquetados (ver cargador PAN en `aleph.menard.pan`).
- Umbral por defecto: 0.5. El puntaje está calibrado al prior del dataset (586 positivos sobre 122064 pares, 0.48 %).

## Resultado global

| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | P@R=0.5 | P@R=0.8 |
|---|---|---|---|---|---|---|---|---|
| Todos los pares | 122064 | 586 | 0.987 | 0.058 | 0.873 | 0.597 | 0.942 | 0.523 |

- Precisión con recall fijo: P@R=0.5 = 0.942, P@R=0.8 = 0.523, P@R=0.9 = 0.154.
- Brier: 0.00193. AUC por mundo: 0.984, 0.991, 0.993, 0.980.
- Clusters (co-pertenencia de pares, umbral 0.5): precisión 0.928, recall 0.590.

## Por cantidad de publicaciones (la cuenta con menos posts del par)

| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | P@R=0.5 | P@R=0.8 |
|---|---|---|---|---|---|---|---|---|
| 5–12 | 40027 | 198 | 0.975 | 0.082 | 0.944 | 0.424 | 0.861 | 0.288 |
| 13–30 | 44755 | 217 | 0.991 | 0.055 | 0.892 | 0.645 | 0.982 | 0.677 |
| 31–80 | 33300 | 155 | 0.993 | 0.038 | 0.820 | 0.735 | 1.000 | 0.646 |
| 81+ | 3982 | 16 | 0.999 | 0.005 | 0.750 | 0.750 | 1.000 | 0.765 |

## Misma plataforma vs. distinta plataforma

| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | P@R=0.5 | P@R=0.8 |
|---|---|---|---|---|---|---|---|---|
| misma plataforma | 47839 | 228 | 0.993 | 0.048 | 0.926 | 0.662 | 0.991 | 0.594 |
| distinta plataforma | 74225 | 358 | 0.984 | 0.064 | 0.836 | 0.556 | 0.895 | 0.432 |

## Casos difíciles

Positivos restringidos al subconjunto indicado, contra todos los negativos.

| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | P@R=0.5 | P@R=0.8 |
|---|---|---|---|---|---|---|---|---|
| con cuenta disfrazada | 121635 | 157 | 0.957 | 0.115 | 0.261 | 0.115 | 0.092 | 0.018 |
| sin disfraz | 121907 | 429 | 0.999 | 0.016 | 0.867 | 0.774 | 0.995 | 0.835 |

Negativos de estilo clonado (personas distintas generadas a partir del mismo arquetipo de idiolecto):

| Negativos | Pares | Falsos positivos @0.5 | AUC contra los positivos |
|---|---|---|---|
| negativos de estilo clonado | 705 | 6.67 % | 0.814 |
| resto de negativos | 120773 | 0.00 % | 0.988 |

## Ablación por familia de señales

Sin reentrenar: se usan los mismos pesos y se quitan señales. Por eso la precisión y el recall a umbral fijo de las filas «solo» no están calibrados; mirar AUC y EER.

| Familia | AUC solo esa familia | EER solo | AUC sin esa familia | EER sin | Recall@0.5 sin | Precisión@0.5 sin |
|---|---|---|---|---|---|---|
| stylometry | 0.975 | 0.082 | 0.949 | 0.125 | 0.205 | 0.992 |
| temporal | 0.690 | 0.367 | 0.986 | 0.061 | 0.584 | 0.875 |
| behavior | 0.894 | 0.184 | 0.973 | 0.082 | 0.474 | 0.861 |
| network | 0.630 | 0.414 | 0.987 | 0.058 | 0.608 | 0.846 |
| profile | 0.766 | 0.309 | 0.988 | 0.053 | 0.297 | 0.930 |

## Señales individuales

AUC de cada señal por separado, solo sobre los pares donde está disponible.

| Señal | Cobertura | Positivos cubiertos | AUC |
|---|---|---|---|
| `stylometry.char_ngrams` | 99.6 % | 586 | 0.902 |
| `stylometry.function_words` | 98.2 % | 572 | 0.821 |
| `stylometry.punctuation` | 97.2 % | 568 | 0.865 |
| `stylometry.capitalization` | 97.2 % | 568 | 0.741 |
| `stylometry.elongation` | 97.2 % | 568 | 0.822 |
| `stylometry.emoji` | 97.6 % | 573 | 0.861 |
| `stylometry.orthography` | 93.1 % | 567 | 0.844 |
| `stylometry.rioplatense` | 82.7 % | 531 | 0.795 |
| `stylometry.length` | 97.2 % | 568 | 0.786 |
| `temporal.hourly` | 79.7 % | 460 | 0.641 |
| `temporal.weekday` | 64.1 % | 369 | 0.681 |
| `temporal.sleep_window` | 40.6 % | 239 | 0.635 |
| `temporal.sync_bursts` | 51.7 % | 333 | 0.796 |
| `temporal.alternation` | 0.0 % | 9 | 0.910 |
| `behavior.client` | 28.2 % | 167 | 0.677 |
| `behavior.hashtags` | 40.5 % | 264 | 0.884 |
| `behavior.domains` | 25.2 % | 189 | 0.790 |
| `behavior.targets` | 92.4 % | 538 | 0.827 |
| `behavior.post_mix` | 88.2 % | 521 | 0.682 |
| `network.following` | 38.4 % | 229 | 0.854 |
| `network.followers` | 38.4 % | 229 | 0.823 |
| `network.mutual_mentions` | 36.5 % | 209 | 0.607 |
| `profile.handle` | 100.0 % | 586 | 0.628 |
| `profile.display_name` | 100.0 % | 586 | 0.587 |
| `profile.bio` | 57.0 % | 317 | 0.672 |
| `profile.creation_date` | 100.0 % | 586 | 0.636 |
| `profile.avatar` | 88.4 % | 510 | 0.559 |

## Modo sin cohorte (`compare(a, b)` sin `background`)

Sin conjunto de referencia no hay IDF ni normalización por cohorte; se usan estadísticas de referencia del paquete y un segundo juego de pesos.

| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | P@R=0.5 | P@R=0.8 |
|---|---|---|---|---|---|---|---|---|
| Todos los pares | 122064 | 586 | 0.978 | 0.082 | 0.810 | 0.531 | 0.867 | 0.363 |

## Rendimiento

Medido en la máquina de desarrollo, un solo proceso, sobre 200 cuentas sintéticas (9438 publicaciones, 19900 pares):

- extracción de rasgos por cuenta: 1.97 s
- señales + fusión (todas las matrices N×N, con rasgos ya extraídos): 1.23 s
- `analyze()` completo (rasgos, señales, fusión, evidencia de 123 pares y 39 clusters): **5.19 s**

## Dónde falla

Lecturas de las tablas de arriba, sin maquillaje:

- Al umbral 0.5 el motor recupera 60 % de los pares del mismo operador con precisión 87 %: el resto de los positivos queda por debajo del umbral.
- Con pocas publicaciones (5–12) el recall cae a 0.424 (AUC 0.975) contra 0.750 (AUC 0.999) con 81+.
- Multiplataforma: recall 0.556 y AUC 0.984 entre plataformas distintas, contra 0.662 y 0.993 en la misma plataforma.
- Cuentas disfrazadas: recall 0.115 (AUC 0.957) contra 0.774 (AUC 0.999) sin disfraz.
- Estilos clonados: 6.67 % de falsos positivos entre personas distintas con idiolecto casi igual, contra 0.003 % en el resto.
- Sin cohorte (`compare` de un par aislado) el AUC es 0.978 y el recall al umbral 0.531.
- Señales que por sí solas aportan poco en este dataset (AUC < 0.65): `temporal.hourly`, `temporal.sleep_window`, `network.mutual_mentions`, `profile.handle`, `profile.display_name`, `profile.creation_date`, `profile.avatar`.
- Señales casi nunca disponibles (< 2 % de los pares), por lo que su peso está poco respaldado: `temporal.alternation`.

## Pendiente

- Evaluar con datasets públicos de verificación de autoría (formato PAN): el cargador está en `aleph.menard.pan`, no se descargó ni corrió nada.
- Ablación con reentrenamiento por familia y curvas de calibración.
- Señal neuronal (familia `neural`): solo está la interfaz.
