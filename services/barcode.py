# -*- coding: utf-8 -*-
"""Normalización de códigos de barras para cruzar la oferta de las droguerías
con los productos propios.

Cada droguería escribe el mismo código de forma distinta:

- UPC-A de 12 dígitos frente al mismo EAN-13 con un cero delante
  ('612197152006' y '0612197152006');
- GTIN-14 en INTERCONTINENTAL ('17593255000418'), que es el EAN-13 del
  artículo ('7593255000411') con un dígito indicador delante y otro dígito de
  control;
- espacios, guiones o letras sueltas.

La forma normal es el GTIN-13 (EAN-13): solo dígitos; los de 8 a 14 se
rellenan con ceros a la izquierda y, si es un GTIN-14 con indicador, se
convierte al artículo recalculando el dígito de control. Lo que no tiene
longitud de GTIN (códigos internos cortos, códigos mal escritos de 15 o más
dígitos) se deja en dígitos sin ceros a la izquierda, para que al menos case
consigo mismo.
"""
import re

_NON_DIGITS = re.compile(r"\D")


def gtin_check_digit(body):
    """Dígito de control GS1 de `body` (los dígitos sin el de control)."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def normalize_barcode(value):
    """Código normalizado (str) o False si no tiene dígitos.

    >>> normalize_barcode('17593255000418')
    '7593255000411'
    >>> normalize_barcode('612197152006')
    '0612197152006'
    """
    digits = _NON_DIGITS.sub("", value or "")
    if not digits:
        return False
    if 8 <= len(digits) <= 14:
        gtin = digits.zfill(14)
        if gtin[0] != "0":
            # Indicador de empaque: el artículo es el resto sin el control.
            body = gtin[1:13]
            gtin = "0" + body + gtin_check_digit(body)
        return gtin[1:]
    return digits.lstrip("0") or "0"
