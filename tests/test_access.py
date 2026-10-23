# -*- coding: utf-8 -*-
"""Quién accede al módulo: solo el personal de compras y los administradores."""
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
        for user in (self.comprador, self.responsable, self.admin):
            self.assertIn(self.chat_menu.id, self._menus(user), user.login)
        self.assertIn(self.config_menu.id, self._menus(self.admin))
        self.assertNotIn(self.config_menu.id, self._menus(self.comprador))
        self.assertNotIn(self.root_menu.id, self._menus(self.empleado))

    def test_actions_by_url(self):
        ai_group = self.env.ref(AI_GROUP)
        admin_group = self.env.ref("base.group_system")
        for xmlid in ("action_ai_v_oferta", "action_ai_barcode_mismatch"):
            self.assertEqual(self.env.ref("dromax_asistente_compras." + xmlid).groups_id, ai_group, xmlid)
        for xmlid in ("action_ai_usage_log", "action_ai_purchase_settings"):
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
        for model in ("ai.v.oferta", "ai.barcode.mismatch", "ai.purchase.chat.session"):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.empleado).search([], limit=1)
            self.env[model].with_user(self.comprador).search([], limit=1)
