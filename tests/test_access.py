# -*- coding: utf-8 -*-
"""Quién accede al módulo: solo el personal de compras y los administradores, y qué ve cada uno."""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

AI_GROUP = "dromax_asistente_compras.group_ai_purchase_user"


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.comprador = new_test_user(cls.env, login="ai_acc_compras",
                                      groups="base.group_user,purchase.group_purchase_user")
        cls.responsable = new_test_user(cls.env, login="ai_acc_resp",
                                        groups="base.group_user,purchase.group_purchase_manager")
        cls.admin = new_test_user(cls.env, login="ai_acc_admin", groups="base.group_user,base.group_system")
        cls.empleado = new_test_user(cls.env, login="ai_acc_empleado",
                                     groups="base.group_user,stock.group_stock_user,sales_team.group_sale_salesman")
        cls.root_menu = cls.env.ref("dromax_asistente_compras.menu_ai_purchase_root")
        cls.chat_menu = cls.env.ref("dromax_asistente_compras.menu_ai_purchase_chat")
        cls.config_menu = cls.env.ref("dromax_asistente_compras.menu_ai_purchase_config")

    def _menus(self, user):
        return self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()

    def test_purchase_staff_and_admins_get_the_group(self):
        for user in (self.comprador, self.responsable, self.admin):
            self.assertTrue(user.has_group(AI_GROUP), user.login)
        self.assertFalse(self.empleado.has_group(AI_GROUP))

    def test_group_is_not_a_user_checkbox(self):
        """No sale en la ficha del usuario: no se le puede dar a alguien ajeno a compras."""
        group = self.env.ref(AI_GROUP)
        self.assertEqual(group.category_id, self.env.ref("base.module_category_hidden"))
        self.assertFalse(group.implied_ids & self.env.ref("purchase.group_purchase_user"),
                         "Compras implica el grupo, no al revés")

    def test_menus(self):
        """Compras ve Propuestas, Reabastecimiento y Chat; lo demás es de administradores."""
        staff_menus = [self.env.ref("dromax_asistente_compras." + xmlid).id for xmlid in (
            "menu_ai_purchase_proposal", "menu_ai_v_reabastecimiento", "menu_ai_purchase_chat")]
        admin_menus = [self.env.ref("dromax_asistente_compras." + xmlid).id for xmlid in (
            "menu_ai_proveedores", "menu_ai_v_comparador", "menu_ai_v_oferta", "menu_ai_barcode_mismatch",
            "menu_ai_analisis", "menu_ai_v_faltantes", "menu_ai_v_rotacion", "menu_ai_v_stock",
            "menu_ai_v_pendiente", "menu_ai_purchase_config")]
        for user in (self.comprador, self.responsable, self.admin):
            visible = self._menus(user)
            for menu_id in staff_menus:
                self.assertIn(menu_id, visible, user.login)
        for user in (self.comprador, self.responsable):
            visible = self._menus(user)
            for menu_id in admin_menus:
                self.assertNotIn(menu_id, visible, user.login)
        visible = self._menus(self.admin)
        for menu_id in admin_menus:
            self.assertIn(menu_id, visible)
        self.assertNotIn(self.root_menu.id, self._menus(self.empleado))

    def test_app_opens_proposals(self):
        """Al entrar a Agente IA se abre Propuestas (antes, el Chat)."""
        proposals = self.env.ref("dromax_asistente_compras.action_ai_purchase_proposal")
        for user in (self.comprador, self.admin):
            menus = self.env["ir.ui.menu"].with_user(user).load_web_menus(False)
            self.assertEqual(menus[self.root_menu.id]["actionID"], proposals.id, user.login)

    def test_actions_by_url(self):
        ai_group = self.env.ref(AI_GROUP)
        admin_group = self.env.ref("base.group_system")
        for xmlid in ("action_ai_purchase_proposal", "action_ai_purchase_proposal_line"):
            self.assertEqual(self.env.ref("dromax_asistente_compras." + xmlid).groups_id, ai_group, xmlid)
        for xmlid in ("action_ai_v_comparador", "action_ai_v_oferta", "action_ai_barcode_mismatch",
                      "action_ai_v_rotacion", "action_ai_v_stock", "action_ai_v_pendiente",
                      "action_ai_v_faltantes", "action_ai_usage_log", "action_ai_purchase_settings"):
            self.assertEqual(self.env.ref("dromax_asistente_compras." + xmlid).groups_id, admin_group, xmlid)

    def test_chat_for_outsiders(self):
        Session = self.env["ai.purchase.chat.session"]
        with self.assertRaisesRegex(AccessError, "personal de compras"):
            Session.with_user(self.empleado).chat_bootstrap()
        with self.assertRaisesRegex(AccessError, "personal de compras"):
            Session.with_user(self.empleado).chat_new_session()
        self.assertIn("presets", Session.with_user(self.comprador).chat_bootstrap())
        self.assertIn("presets", Session.with_user(self.admin).chat_bootstrap())

    def test_data_for_outsiders(self):
        for model in ("ai.v.oferta", "ai.barcode.mismatch", "ai.purchase.chat.session",
                      "ai.v.rotacion", "ai.v.stock", "ai.v.pendiente", "ai.v.faltantes",
                      "ai.purchase.proposal", "ai.purchase.proposal.line"):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.empleado).search([], limit=1)
            self.env[model].with_user(self.comprador).search([], limit=1)
