-- Rol de solo lectura del Asistente de Compras IA (segunda barrera tras el
-- validador de SQL del módulo). Ejecutar como superusuario de Postgres,
-- conectado a la base de datos de Odoo:
--
--   psql -d <base> -v ro_password='<clave>' -f create_readonly_role.sql
--
-- Es idempotente: se puede volver a lanzar para añadir tablas nuevas de la
-- lista blanca (por ejemplo, cuando el módulo cree las vistas ai_v_*).
-- La lista debe coincidir con DEFAULT_WHITELIST de services/sql_validator.py
-- o con el parámetro dromax_asistente_compras.sql_whitelist.

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE dromax_ai_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dromax_ai_ro') \gexec

ALTER ROLE dromax_ai_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
SELECT format('ALTER ROLE dromax_ai_ro PASSWORD %L', :'ro_password') \gexec
ALTER ROLE dromax_ai_ro SET default_transaction_read_only = on;
ALTER ROLE dromax_ai_ro SET statement_timeout = '30s';
ALTER ROLE dromax_ai_ro SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE dromax_ai_ro CONNECTION LIMIT 10;

-- Nada por defecto: ni crear objetos ni leer lo que no esté en la lista.
REVOKE CREATE ON SCHEMA public FROM dromax_ai_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dromax_ai_ro;
GRANT USAGE ON SCHEMA public TO dromax_ai_ro;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'ai_v_rotacion', 'ai_v_stock', 'ai_v_pendiente', 'ai_v_faltantes', 'ai_v_oferta',
        'sale_order', 'sale_order_line', 'pos_order', 'pos_order_line',
        'purchase_order', 'purchase_order_line',
        'stock_quant', 'stock_location', 'stock_warehouse',
        'product_product', 'product_template', 'product_category', 'uom_uom',
        'res_company'
    ] LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('GRANT SELECT ON public.%I TO dromax_ai_ro', t);
        ELSE
            RAISE NOTICE 'No existe todavía, se omite: %', t;
        END IF;
    END LOOP;
END $$;
