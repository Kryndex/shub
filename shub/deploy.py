from __future__ import absolute_import
import os
import glob
import shutil
import tempfile
from six.moves.urllib.parse import urljoin

import click
# Not used in code but needed in runtime, don't remove!
import setuptools
_1 = setuptools  # NOQA
try:
    # Only available in setuptools >= 24.0.0
    import setuptools.msvc
except ImportError:
    pass
else:
    _2 = setuptools.msvc  # NOQA


from shub.config import load_shub_config
from shub.exceptions import NotFoundException, ShubException
from shub.utils import (closest_file, create_scrapinghub_yml_wizard,
                        get_config, inside_project, make_deploy_request,
                        run_python)


HELP = """
Deploy the current folder's Scrapy project to Scrapy Cloud.

If you do not supply `target`, the default target from scrapinghub.yml will be
used. If you have no scrapinghub.yml, you will be guided through a short wizard
to create one. You can also specify a numerical project ID:

    shub deploy 12345

Or use any of the targets defined in your scrapinghub.yml:

    shub deploy production

To see a list of all defined targets, run:

    shub deploy -l

You can also deploy an existing project egg:

    shub deploy --egg egg_name

Or build an egg without deploying:

    shub deploy --build-egg egg_name
"""

SHORT_HELP = "Deploy Scrapy project to Scrapy Cloud"


_SETUP_PY_TEMPLATE = """\
# Automatically created by: shub deploy

from setuptools import setup, find_packages

setup(
    name         = 'project',
    version      = '1.0',
    packages     = find_packages(),
    entry_points = {'scrapy': ['settings = %(settings)s']},
)
"""


def list_targets(ctx, param, value):
    if not value:
        return
    conf = load_shub_config()
    for name in conf.projects:
        click.echo(name)
    ctx.exit()


@click.command(help=HELP, short_help=SHORT_HELP)
@click.argument("target", required=False, default="default")
@click.option("-l", "--list-targets", help="list available targets",
              is_flag=True, is_eager=True, expose_value=False,
              callback=list_targets)
@click.option("-V", "--version", help="the version to use for deploying")
@click.option("-d", "--debug", help="debug mode (do not remove build dir)",
              is_flag=True)
@click.option("--egg", help="deploy the given egg, instead of building one")
@click.option("--build-egg", help="only build the given egg, don't deploy it")
@click.option("-v", "--verbose", help="stream deploy logs to console",
              is_flag=True)
@click.option("-k", "--keep-log", help="keep the deploy log", is_flag=True)
def cli(target, version, debug, egg, build_egg, verbose, keep_log):
    if not inside_project():
        raise NotFoundException("No Scrapy project found in this location.")
    tmpdir = None
    try:
        if build_egg:
            egg, tmpdir = _build_egg()
            click.echo("Writing egg to %s" % build_egg)
            shutil.copyfile(egg, build_egg)
        else:
            conf = load_shub_config()
            if target == 'default' and target not in conf.projects:
                create_scrapinghub_yml_wizard(conf)
            targetconf = conf.get_target_conf(target)
            version = version or targetconf.version
            auth = (targetconf.apikey, '')

            if egg:
                click.echo("Using egg: %s" % egg)
                egg = egg
            else:
                click.echo("Packing version %s" % version)
                egg, tmpdir = _build_egg()

            _upload_egg(targetconf.endpoint, egg, targetconf.project_id,
                        version, auth, verbose, keep_log, targetconf.stack,
                        targetconf.requirements_file, targetconf.eggs)
            click.echo("Run your spiders at: "
                       "https://app.scrapinghub.com/p/%s/"
                       "" % targetconf.project_id)
    finally:
        if tmpdir:
            if debug:
                click.echo("Output dir not removed: %s" % tmpdir)
            else:
                shutil.rmtree(tmpdir, ignore_errors=True)


def _url(endpoint, action):
    return urljoin(endpoint, action)


def _upload_egg(endpoint, eggpath, project, version, auth, verbose, keep_log,
                stack=None, requirements_file=None, eggs=None):
    expanded_eggs = []
    for e in (eggs or []):
        # Expand glob patterns, but make sure we don't swallow non-existing
        # eggs that were directly named
        # (glob.glob('non_existing_file') returns [])
        if any(['*' in e, '?' in e, '[' in e and ']' in e]):
            # Never match the main egg
            expanded_eggs.extend(
                [x for x in glob.glob(e)
                 if os.path.abspath(x) != os.path.abspath(eggpath)])
        else:
            expanded_eggs.append(e)

    data = {'project': project, 'version': version}
    if stack:
        data['stack'] = stack

    try:
        files = [('eggs', open(path, 'rb')) for path in expanded_eggs]
        if requirements_file:
            files.append(('requirements', open(requirements_file, 'rb')))
    except IOError as e:
        raise ShubException("%s %s" % (e.strerror, e.filename))
    files.append(('egg', open(eggpath, 'rb')))
    url = _url(endpoint, 'scrapyd/addversion.json')
    click.echo('Deploying to Scrapy Cloud project "%s"' % project)
    return make_deploy_request(url, data, files, auth, verbose, keep_log)


def _build_egg():
    closest = closest_file('scrapy.cfg')
    os.chdir(os.path.dirname(closest))
    if not os.path.exists('setup.py'):
        settings = get_config().get('settings', 'default')
        _create_default_setup_py(settings=settings)
    d = tempfile.mkdtemp(prefix="shub-deploy-")
    run_python(['setup.py', 'clean', '-a', 'bdist_egg', '-d', d])
    egg = glob.glob(os.path.join(d, '*.egg'))[0]
    return egg, d


def _create_default_setup_py(**kwargs):
    with open('setup.py', 'w') as f:
        f.write(_SETUP_PY_TEMPLATE % kwargs)
