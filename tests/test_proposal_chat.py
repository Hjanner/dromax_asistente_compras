# -*- coding: utf-8 -*-
"""Propuesta en el chat: get_proposal, update_proposal_lines y los botones."""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from ..services import openrouter
from ..services.proposal_tools import GET_PROPOSAL, UPDATE_PROPOSAL_LINES, answer_laboratories
from ..services.tools import call_tool, tool_definitions
from .test_analisis import AnalisisCase
from .test_chat import ScriptedTransport, text_response
from .test_oferta import _ean

AI_GROUPS = "base.group_user,dromax_asistente_compras.group_ai_purchase_user"


def call_response(name, args, call_id="call_1"):
    return {
        "id": "gen-tool", "model": "fake/model",
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.002},
    }


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestProposalChat(AnalisisCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.openrouter_api_key", "sk-or-test")
        cls.ibuprofeno.write({"name": "ZQX IBUPROFENO 400MG [lab zqx]", "barcode": _ean("759999900093")})
        cls.vitamina.write({"name": "ZQX VITAMINA C [otro zqx]", "barcode": _ean("759999900094")})
        Partner = cls.env["res.partner"]
        cls.drog_a = Partner.create({"name": "ZQX DROG ALFA", "is_company": True})
        cls.drog_b = Partner.create({"name": "ZQX DROG BETA", "is_company": True})
        cls.paracetamol = cls.env["product.product"].create({
            "name": "ZQX PARACETAMOL [lab zqx]", "detailed_type": "product", "barcode": _ean("759999900095")})
        # Comprador con acceso a la farmacia y a la matriz, con solo la farmacia marcada en el selector.
        cls.buyer = new_test_user(cls.env, login="ai_proposal_chat_buyer",
                                  groups=AI_GROUPS + ",purchase.group_purchase_user",
                                  company_id=cls.company.id, company_ids=[cls.company.id, cls.matriz.id])
        cls.Proposal = cls.env["ai.purchase.proposal"]
        cls.Line = cls.env["ai.purchase.proposal.line"]

    def setUp(self):
        super().setUp()
        # Que la propuesta vigente sea la de cada test.
        self.Proposal.search([]).write({"state": "discarded"})
        self.uenv = self.env(user=self.buyer, context=dict(self.env.context, allowed_company_ids=[self.company.id]))

    def _proposal(self):
        """Compra de la matriz: ibuprofeno (2 droguerías; a la farmacia le faltan 7), vitamina (a
        revisar por presentación) y paracetamol (del mismo laboratorio, falta en otra farmacia)."""
        self._offer(self.drog_a, self.ibuprofeno, 1.5, 100)
        self._offer(self.drog_b, self.ibuprofeno, 2.0, 100)
        self._offer(self.drog_a, self.vitamina, 50.0, 100)
        self._offer(self.drog_a, self.paracetamol, 1.5, 100)
        self._offer(self.drog_b, self.paracetamol, 2.0, 100)
        self._weekly_sales(self.ibuprofeno, 10)
        self._weekly_sales(self.vitamina, 4)
        self._set_stock(self.ibuprofeno, 3)
        self.env.flush_all()
        proposal = self.Proposal._generate()
        (proposal.line_ids - self._line(proposal, self.ibuprofeno) - self._line(proposal, self.vitamina)).unlink()
        self.vit = self._line(proposal, self.vitamina)
        self.vit.write({"costo": 1.0, "revisar_presentacion": True})
        self.ibu = self._line(proposal, self.ibuprofeno)
        self.para = self.Line.create({
            "proposal_id": proposal.id, "company_id": self.matriz.id, "product_id": self.paracetamol.id,
            "laboratorio": "LAB ZQX", "cantidad_sugerida": 5, "cantidad_final": 5,
            "faltas_farmacias": 5, "detalle_farmacias": "ZQX FARMACIA 2 (5)",
            "drogueria_sugerida_id": self.drog_a.id, "drogueria_id": self.drog_a.id,
            "precio_sugerido": 1.5, "precio": 1.5})
        return proposal

    def _line(self, proposal, product):
        line = proposal.line_ids.filtered(lambda l: l.product_id == product and l.company_id == self.matriz)
        self.assertEqual(len(line), 1)
        return line

    def _tool(self, name, **args):
        return call_tool(self.uenv, name, args)

    # -- get_proposal -----------------------------------------------------------

    def test_tools_are_offered(self):
        names = [t["function"]["name"] for t in tool_definitions(self.uenv)]
        self.assertEqual(names, ["run_readonly_sql", "get_proposal", "update_proposal_lines"])
        self.assertIn(GET_PROPOSAL, tool_definitions(self.uenv))
        self.assertIn(UPDATE_PROPOSAL_LINES, tool_definitions(self.uenv))

    def test_no_proposal(self):
        res = self._tool("get_proposal")
        self.assertIn("Todavía no hay ninguna propuesta", res["error"])

    def test_summary(self):
        proposal = self._proposal()
        res = self._tool("get_proposal")
        self.assertEqual(res["propuesta"]["id"], proposal.id)
        self.assertEqual(res["propuesta"]["estado"], "Lista")
        # Las líneas son de la matriz, aunque en el selector solo esté marcada la farmacia.
        self.assertEqual(res["lineas"], 3)
        self.assertEqual(res["compra"], "ZQX MATRIZ")
        self.assertEqual(res["monto_final"], round(7 * 1.5 + 4 * 50.0 + 5 * 1.5, 2))
        self.assertEqual(res["atencion"]["revisar_presentacion"], 1)
        self.assertNotIn("con_excedente", res["atencion"])
        self.assertEqual(res["atencion"]["por_pedir"], 3)
        labs = {l["laboratorio"]: l for l in res["laboratorios_mayor_monto"]}
        self.assertEqual(labs["LAB ZQX"]["lineas"], 2)
        self.assertEqual(labs["OTRO ZQX"]["revisar_presentacion"], 1)
        self.assertEqual(res["laboratorios_total"], 2)
        self.assertEqual([l["linea_id"] for l in res["revisar_presentacion_mayor_monto"]], [self.vit.id])
        self.assertEqual(res["revisar_presentacion_mayor_monto"][0]["costo"], 1.0)
        self.assertNotIn("traslados_que_cubren_todo", res)

    def test_lines_filters(self):
        proposal = self._proposal()
        res = self._tool("get_proposal", laboratorio="lab zqx")
        self.assertEqual({l["linea_id"] for l in res["lineas"]}, {self.ibu.id, self.para.id})
        line = next(l for l in res["lineas"] if l["linea_id"] == self.ibu.id)
        self.assertEqual((line["cantidad_sugerida"], line["cantidad_final"], line["drogueria"]), (7, 7, "ZQX DROG ALFA"))
        self.assertIn("farmacias 7", line["motivo"])
        self.assertEqual((line["falta_farmacias"], line["falta_por_farmacia"]), (7, "%s (7)" % self.company.name))
        # Las ofertas de las demás droguerías, de la más barata a la más cara.
        self.assertEqual([o["drogueria"] for o in line["ofertas"]], ["ZQX DROG ALFA", "ZQX DROG BETA"])
        res = self._tool("get_proposal", buscar="vitamina")
        self.assertEqual([l["linea_id"] for l in res["lineas"]], [self.vit.id])
        self.assertTrue(res["lineas"][0]["revisar_presentacion"])
        self.assertEqual(res["lineas"][0]["costo"], 1.0)
        # Por farmacia: las líneas en que a esa farmacia le falta el producto.
        res = self._tool("get_proposal", farmacia="farmacia 2")
        self.assertEqual([l["linea_id"] for l in res["lineas"]], [self.para.id])
        res = self._tool("get_proposal", filtro="revisar_presentacion")
        self.assertEqual([l["linea_id"] for l in res["lineas"]], [self.vit.id])
        res = self._tool("get_proposal", laboratorio="lab zqx", limite=1)
        self.assertEqual((res["lineas_encontradas"], res["row_count"], res["truncated"]), (2, 1, True))
        self.assertEqual(res["monto_encontrado"], 7 * 1.5 + 5 * 1.5)                # de todas, no solo la página
        # Un laboratorio mal escrito: sin líneas, con los nombres parecidos.
        res = self._tool("get_proposal", laboratorio="lav zqx")
        self.assertEqual(res["lineas"], [])
        self.assertEqual(res["laboratorios_parecidos"][0], "LAB ZQX")
        res = self._tool("get_proposal", buscar="no existe zqx")
        self.assertEqual(res["lineas"], [])
        self.assertIn("Ninguna línea", res["nota"])
        self.assertIn("error", self._tool("get_proposal", filtro="otro"))
        self.assertIn("error", self._tool("get_proposal", propuesta_id=999999999))
        # Una propuesta anterior, por su id.
        proposal.state = "discarded"
        self.assertEqual(self._tool("get_proposal", propuesta_id=proposal.id)["propuesta"]["estado"], "Descartada")

    def test_no_transfers_between_pharmacies(self):
        """Ya no se sugieren traslados entre farmacias: ni filtro ni campos de excedente."""
        self._proposal()
        self.assertIn("error", self._tool("get_proposal", filtro="se_cubre_con_traslado"))
        self.assertIn("error", self._tool("get_proposal", filtro="con_excedente"))
        self.assertNotIn("excedente_otras", self.Line._fields)

    def test_without_matrix_access(self):
        self._proposal()
        self.buyer.company_ids = self.company                           # sin la matriz: no ve la compra
        self.assertEqual(self._tool("get_proposal")["lineas"], 0)
        res = self._tool("update_proposal_lines", cambios=[{"linea_id": self.para.id, "cantidad_final": 1}])
        self.assertIn("no tienes acceso", res["resultados"][0]["error"])
        self.assertEqual(self.para.cantidad_final, 5)

    # -- update_proposal_lines -----------------------------------------------------

    def test_update_lines(self):
        proposal = self._proposal()
        res = self._tool("update_proposal_lines", cambios=[
            {"linea_id": self.ibu.id, "cantidad_final": 12, "nota": "viene la temporada de gripe"},
            {"linea_id": self.para.id, "drogueria": "beta"},
            {"linea_id": self.vit.id, "cantidad_final": 0},
        ])
        self.assertEqual(res["row_count"], 3)
        first = res["resultados"][0]
        self.assertEqual((first["antes"]["cantidad_final"], first["ahora"]["cantidad_final"]), (7, 12))
        self.assertEqual(self.ibu.cantidad_final, 12)
        self.assertIn("Ajuste en el chat: viene la temporada de gripe", self.ibu.motivo)
        self.assertIn("farmacias 7", self.ibu.motivo)
        # Otra droguería: el precio sale de su oferta.
        self.assertEqual((self.para.drogueria_id, self.para.precio), (self.drog_b, 2.0))
        self.assertEqual(res["resultados"][1]["ahora"]["precio"], 2.0)
        self.assertEqual(self.vit.cantidad_final, 0)
        self.assertEqual(proposal.state, "in_review")
        self.assertEqual(res["propuesta"]["lineas_ajustadas"], 3)
        body = proposal.message_ids[0].body
        self.assertIn("Ajustes desde el chat", body)
        self.assertIn("cantidad 7 → 12", body)
        self.assertIn("droguería ZQX DROG ALFA → ZQX DROG BETA", body)

    def test_update_errors(self):
        proposal = self._proposal()
        res = self._tool("update_proposal_lines", cambios=[
            {"linea_id": self.ibu.id, "drogueria": "gamma"},
            {"linea_id": self.ibu.id, "drogueria": "zqx drog"},
            {"linea_id": self.ibu.id, "cantidad_final": -3},
            {"linea_id": self.ibu.id},
            {"linea_id": 999999999, "cantidad_final": 1},
            {"linea_id": self.vit.id, "cantidad_final": 2},
        ])
        errors = [r.get("error") for r in res["resultados"]]
        self.assertIn("Droguerías con oferta: ZQX DROG ALFA, ZQX DROG BETA", errors[0])
        self.assertIn("varias droguerías", errors[1])
        self.assertIn("mayor o igual a 0", errors[2])
        self.assertIn("Nada que cambiar", errors[3])
        self.assertIn("no existe", errors[4])
        self.assertIsNone(errors[5])                                   # las válidas sí se aplican
        self.assertEqual((self.ibu.cantidad_final, self.ibu.drogueria_id), (7, self.drog_a))
        self.assertEqual(self.vit.cantidad_final, 2)
        self.assertIn("error", self._tool("update_proposal_lines", cambios=[]))
        # Una línea ya pedida se corrige en la orden; una propuesta cerrada, no se toca.
        self.ibu.purchase_line_id = self._purchase(self.ibuprofeno, 1, confirm=False).order_line
        res = self._tool("update_proposal_lines", cambios=[{"linea_id": self.ibu.id, "cantidad_final": 3}])
        self.assertIn("ya está en una orden de compra", res["resultados"][0]["error"])
        proposal.state = "discarded"
        res = self._tool("update_proposal_lines", cambios=[{"linea_id": self.vit.id, "cantidad_final": 3}])
        self.assertIn("cerrada", res["resultados"][0]["error"])

    # -- chat ---------------------------------------------------------------------

    def _transport(self, *responses):
        transport = ScriptedTransport(*responses)
        patcher = patch.object(openrouter, "Transport", lambda: transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        return transport

    def test_chat_adjusts_and_generates_drafts(self):
        proposal = self._proposal()
        self._transport(
            call_response("update_proposal_lines", {"cambios": [{"linea_id": self.ibu.id, "cantidad_final": 20}]}),
            text_response("Listo: ibuprofeno de 7 a 20."))
        session = self.env["ai.purchase.chat.session"].with_env(self.uenv).create({})
        res = session.chat_send(text="del ibuprofeno pide 20", level="medium")
        answer = res["messages"][1]
        self.assertEqual(answer["proposal"], {"id": proposal.id, "name": proposal.name, "state": "in_review",
                                              "laboratorio": "LAB ZQX", "vista": False})
        self.assertEqual(answer["tool_trace"][0]["tool"], "update_proposal_lines")
        self.assertIn("update_proposal_lines(", answer["tool_trace"][0]["query"])
        self.assertEqual(self.ibu.cantidad_final, 20)
        self.assertTrue(session.with_env(self.uenv).chat_bootstrap()["can_create_rfq"])
        # El botón pide las líneas de la matriz aunque en el selector solo esté la farmacia.
        self.vit.cantidad_final = 2                                     # ajustada: entra
        action = session.chat_create_rfq(proposal.id)
        self.assertEqual(proposal.purchase_order_ids.company_id, self.matriz)
        self.assertEqual(set(action["domain"][0][2]), set(proposal.purchase_order_ids.ids))
        self.assertEqual(proposal.state, "done")

    def test_drafts_of_one_lab(self):
        proposal = self._proposal()
        self.vit.cantidad_final = 2                                     # ajustada: entra con todo
        self._transport(
            call_response("get_proposal", {"laboratorio": "lab zqx"}), text_response("LAB ZQX revisado."),
            call_response("get_proposal", {"laboratorio": "zqx"}), text_response("Dos laboratorios."),
            call_response("get_proposal", {}), text_response("Resumen."),
            call_response("get_proposal", {"laboratorio": "nada zqx"}), text_response("No hay."))
        session = self.env["ai.purchase.chat.session"].with_env(self.uenv).create({})

        def proposal_of(text):
            return session.chat_send(text=text, level="medium")["messages"][1]["proposal"]

        self.assertEqual(proposal_of("revisa lab zqx")["laboratorio"], "LAB ZQX")
        self.assertFalse(proposal_of("revisa zqx")["laboratorio"])     # dos laboratorios
        self.assertFalse(proposal_of("resumen")["laboratorio"])        # la propuesta completa
        self.assertFalse(proposal_of("revisa nada zqx"))                # sin líneas: sin botones
        # Solo el laboratorio de la respuesta (sus dos productos).
        session.chat_create_rfq(proposal.id, "LAB ZQX")
        self.assertTrue(self.ibu.purchase_line_id and self.para.purchase_line_id)
        self.assertFalse(self.vit.purchase_line_id)
        self.assertEqual(proposal.state, "in_review")
        with self.assertRaisesRegex(UserError, "no tiene líneas de NADA"):
            session.chat_create_rfq(proposal.id, "NADA")
        session.chat_create_rfq(proposal.id)
        self.assertTrue(self.vit.purchase_line_id)
        self.assertEqual(proposal.state, "done")

    def test_answer_laboratories(self):
        lines = {"lineas": [{"laboratorio": "A"}, {"laboratorio": "B"}]}
        self.assertEqual(answer_laboratories(lines), {"A", "B"})
        # Lista recortada: solo cuenta si el filtro fue de un laboratorio.
        self.assertIsNone(answer_laboratories(dict(lines, truncated=True)))
        self.assertEqual(answer_laboratories(dict(lines, truncated=True, laboratorio="A")), {"A"})
        self.assertIsNone(answer_laboratories({"propuesta": {}, "lineas": 3}))   # resumen
        self.assertEqual(answer_laboratories({"resultados": [
            {"laboratorio": "A"}, {"linea_id": 9, "error": "no existe"}]}), {"A"})

    def test_lab_preset(self):
        self._proposal()
        transport = self._transport(text_response("Revisado"))
        session = self.env["ai.purchase.chat.session"].with_env(self.uenv).create({})
        res = session.chat_send(preset="revisar_laboratorio", text="  lab   zqx ", level="basic")
        self.assertEqual(res["messages"][0]["preset_label"], "Revisa un laboratorio: lab zqx")
        self.assertEqual(res["messages"][1]["level"], "advanced")
        self.assertEqual(session.name, "Revisa un laboratorio: lab zqx")
        self.assertIn("«lab zqx»", transport.payloads[0]["messages"][-1]["content"])
        with self.assertRaisesRegex(UserError, "Escribe el dato"):
            session.chat_send(preset="revisar_laboratorio", text="   ")
        with self.assertRaisesRegex(UserError, "demasiado largo"):
            session.chat_send(preset="revisar_laboratorio", text="x" * 101)
        presets = {p["key"]: p for p in session.chat_bootstrap()["presets"]}
        self.assertTrue(presets["revisar_laboratorio"]["input"])
        self.assertNotIn("traslados", presets)                         # ya no hay traslados entre farmacias
        self.assertFalse(presets["reabastecer"]["input"])

    def test_preset_uses_week_proposal(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self.env.flush_all()
        transport = self._transport(*[text_response("Resumen")] * 3)
        session = self.env["ai.purchase.chat.session"].with_env(self.uenv).create({})
        # Sin propuesta de esta semana, el botón la calcula antes de preguntar.
        session.chat_send(preset="compra_semana")
        proposal = self.Proposal._current()
        self.assertTrue(proposal)
        self.assertIn("Calculada desde el chat", proposal.message_ids[0].body)
        self.assertIn("get_proposal", json.dumps(transport.payloads[0]["tools"]))
        # Con la de esta semana, la reutiliza aunque ya se haya ajustado.
        proposal.line_ids[:1].cantidad_final += 1
        session.chat_send(preset="compra_semana")
        self.assertEqual(self.Proposal._current(), proposal)
        # La de la semana pasada se reemplaza.
        proposal.date_computed = fields.Datetime.now() - timedelta(days=8)
        session.chat_send(preset="compra_semana")
        self.assertNotEqual(self.Proposal._current(), proposal)
        self.assertEqual(proposal.state, "discarded")
