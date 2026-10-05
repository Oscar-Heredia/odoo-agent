from odoo_agent.server import own_modules_only


def test_quita_los_modulos_que_estan_en_las_fuentes(tmp_path):
    addon = tmp_path / "odoo" / "addons" / "l10n_mx"
    addon.mkdir(parents=True)
    (addon / "__manifest__.py").write_text("{}")
    modules = [{"name": "l10n_mx", "author": "Vauxoo"}, {"name": "acme_ventas", "author": "Acme"}]
    assert own_modules_only(modules, tmp_path) == [{"name": "acme_ventas", "author": "Acme"}]


def test_sin_fuentes_no_quita_nada(tmp_path):
    modules = [{"name": "acme_ventas", "author": "Acme"}]
    assert own_modules_only(modules, tmp_path / "no-existe") == modules
