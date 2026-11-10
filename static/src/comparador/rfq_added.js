/** @odoo-module **/
/**
 * Aviso tras añadir productos del comparador a los borradores de compra.
 *
 * Como `display_notification`, pero con un botón «Ver en Compras» que abre esos
 * borradores en la misma pestaña (con la ruta de migas para volver al
 * comparador). Quien prefiera seguir comparando, cierra el aviso.
 *
 * params: title, message, button, action (la de las órdenes), next.
 */
import { registry } from "@web/core/registry";

export function rfqAddedAction(env, action) {
    const params = action.params || {};
    let close = () => {};
    const buttons = params.action
        ? [{
              name: params.button,
              icon: "fa-shopping-cart",
              primary: true,
              onClick: () => {
                  close();
                  env.services.action.doAction(params.action);
              },
          }]
        : [];
    close = env.services.notification.add(params.message, {
        title: params.title,
        type: "success",
        sticky: true,
        buttons,
    });
    return params.next;
}

registry.category("actions").add("dromax_asistente_compras.rfq_added", rfqAddedAction);
