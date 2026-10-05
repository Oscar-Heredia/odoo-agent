from odoo_agent.server import own_modules_only


def test_drops_modules_found_in_the_sources(tmp_path):
    addon = tmp_path / "odoo" / "addons" / "l10n_mx"
    addon.mkdir(parents=True)
    (addon / "__manifest__.py").write_text("{}")
    modules = [{"name": "l10n_mx", "author": "Vauxoo"}, {"name": "acme_sales", "author": "Acme"}]
    assert own_modules_only(modules, tmp_path) == [{"name": "acme_sales", "author": "Acme"}]


def test_without_sources_drops_nothing(tmp_path):
    modules = [{"name": "acme_sales", "author": "Acme"}]
    assert own_modules_only(modules, tmp_path / "missing") == modules
