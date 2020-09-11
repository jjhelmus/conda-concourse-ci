import os
import subprocess

from conda_concourse_ci import execute
import conda_concourse_ci
from conda_concourse_ci.utils import HashableDict

import pytest
import yaml

from .utils import test_data_dir, graph_data_dir, test_config_dir


def test_collect_tasks(mocker, testing_conda_resolve, testing_graph):
    mocker.patch.object(execute, 'Resolve')
    mocker.patch.object(execute, 'get_build_index')
    mocker.patch.object(conda_concourse_ci.compute_build_graph, '_installable')
    execute.Resolve.return_value = testing_conda_resolve
    conda_concourse_ci.compute_build_graph._installable.return_value = True
    task_graph = execute.collect_tasks(graph_data_dir, folders=['a'],
                                       matrix_base_dir=test_config_dir)
    build_platforms = os.listdir(os.path.join(test_config_dir, 'build_platforms.d'))
    # one build, one test per platform, uploads only for builds.
    n_platforms = len(build_platforms)
    # minimum args means build and test provided folders.  Two tasks.
    assert len(task_graph.nodes()) == n_platforms


boilerplate_test_vars = {'base-name': 'steve',
                         'aws-bucket': '123',
                         'aws-key-id': 'abc',
                         'aws-secret-key': 'weee',
                         'aws-region-name': 'frank'}


def test_get_build_task(testing_graph):
    # ensure that our channels make it into the args
    node = 'b-on-linux'
    meta = testing_graph.nodes[node]['meta']
    worker = testing_graph.nodes[node]['worker']
    meta.config.channel_urls = ['conda_build_test']
    task = execute.get_build_task(node, meta, worker)
    assert task['config']['platform'] == 'linux'
    assert task['config']['inputs'] == [{'name': 'rsync-recipes'}]
    assert 'rsync-recipes/b-on-linux' in task['config']['run']['args'][-1]
    assert 'conda_build_test' in task['config']['run']['args'][-1]


def test_graph_to_plan_with_jobs(mocker, testing_graph):
    # stub out uploads, since it depends on config file stuff and we want to manipulate it
    # get_upload = mocker.patch.object(execute, "get_upload_tasks")
    # get_upload.return_value = []

    with open(os.path.join(test_config_dir, 'config.yml')) as f:
        config_vars = yaml.safe_load(f)
    pipeline = execute.graph_to_plan_with_jobs(graph_data_dir, testing_graph, 'abc123',
                                                test_config_dir, config_vars)
    # rsync-recipes, rsync-source, rsync-stats, and one artifact resource per build
    assert len(pipeline.resources) == 6
    # a, b, c
    assert len(pipeline.jobs) == 3


def test_submit(mocker):
    mocker.patch.object(execute, 'subprocess')
    mocker.patch.object(conda_concourse_ci.concourse, 'subprocess')
    pipeline_file = os.path.join(test_config_dir, 'plan_director.yml')
    execute.submit(pipeline_file, base_name="test", pipeline_name="test-pipeline",
                   src_dir='.', config_root_dir=os.path.join(test_data_dir, 'config-test'))


@pytest.mark.serial
def test_submit_one_off(mocker):
    mocker.patch.object(conda_concourse_ci.concourse, 'subprocess')
    check_call = mocker.patch.object(execute.subprocess, 'check_call')
    execute.submit_one_off('frank', os.path.join(test_data_dir, 'one-off-recipes'),
                           folders=('bzip2', 'pytest', 'pytest-cov'),
                           config_root_dir=test_config_dir)
    # basically what we're checking here is that the config_overrides have been passed correctly
    check_call.assert_has_calls([mocker.call(['rsync', '--delete', '-av', '-e',
                               mocker.ANY,  # ssh command that we don't care about much
                               mocker.ANY,  # temp source directory that we don't care about
                               mocker.ANY,  # -p (makes chmod flags work)
                               mocker.ANY,  # chmod flags
                               ('your-intermediate-user@your-intermediate-server:'
                                # this is what we care about.  The middle entry here
                                #    needs 'test' replaced with 'frank'.  Also, we're syncing a
                                #    plan and recipe folder, not a config folder
                                '/ci/frank/plan_and_recipes')
                               ])])

@pytest.mark.serial
def test_submit_batch(mocker):
    mocker.patch.object(execute, 'subprocess')
    mocker.patch.object(conda_concourse_ci.concourse, 'subprocess')
    submit_one_off = mocker.patch.object(execute, 'submit_one_off')
    get_activate_builds = mocker.patch.object(execute, '_get_activate_builds', return_value=3)

    execute.submit_batch(
        os.path.join(test_data_dir, 'batch_sample.txt'),
        os.path.join(test_data_dir, 'one-off-recipes'),
        config_root_dir=test_config_dir,
        max_builds=999, poll_time=0, build_lookback=500, label_prefix='sentinel_')
    # submit_one_off should be called twice
    submit_one_off.assert_has_calls([
        mocker.call('sentinel_bzip', mocker.ANY, ['bzip'],
                    mocker.ANY, pass_throughs=None, clobber_sections_file='example.yaml'),
        mocker.call('sentinel_pytest', mocker.ANY, ['pytest', 'pytest-cov'],
                    mocker.ANY, pass_throughs=None),
    ])
    get_activate_builds.assert_called()


def test_bootstrap(mocker, testing_workdir):
    execute.bootstrap('frank')
    assert os.path.isfile('plan_director.yml')
    assert os.path.isdir('frank')
    assert os.path.isfile('frank/config.yml')
    assert os.path.isdir('frank/uploads.d')
    assert os.path.isdir('frank/build_platforms.d')
    assert os.path.isdir('frank/test_platforms.d')


def test_compute_builds(testing_workdir, mocker, monkeypatch):
    monkeypatch.chdir(test_data_dir)
    output = os.path.join(testing_workdir, 'output')
    # neutralize git checkout so we're really testing the HEAD commit
    execute.compute_builds('.', 'config-name',
                           folders=['python_test', 'conda_forge_style_recipe'],
                           matrix_base_dir=os.path.join(test_data_dir, 'linux-config-test'),
                           output_dir=output)
    assert os.path.isdir(output)
    files = os.listdir(output)
    assert 'plan.yml' in files

    assert os.path.isfile(os.path.join(output, 'frank-1.0-python_2.7-on-centos5-64', 'meta.yaml'))
    assert os.path.isfile(os.path.join(output, 'frank-1.0-python_2.7-on-centos5-64/', 'conda_build_config.yaml'))
    assert os.path.isfile(os.path.join(output, 'frank-1.0-python_3.6-on-centos5-64', 'meta.yaml'))
    assert os.path.isfile(os.path.join(output, 'frank-1.0-python_3.6-on-centos5-64/', 'conda_build_config.yaml'))
    assert os.path.isfile(os.path.join(output, 'dummy_conda_forge_test-1.0-on-centos5-64', 'meta.yaml'))
    with open(os.path.join(output, 'dummy_conda_forge_test-1.0-on-centos5-64/', 'conda_build_config.yaml')) as f:
        cfg = f.read()

    assert cfg is not None
    if hasattr(cfg, 'decode'):
        cfg = cfg.decode()
    assert "HashableDict" not in cfg


def test_compute_builds_intradependencies(testing_workdir, monkeypatch, mocker):
    """When we build stuff, and upstream dependencies are part of the batch, but they're
    also already installable, then we do extra work to make sure that we order our build
    so that downstream builds depend on upstream builds (and don't directly use the
    already-available packages.)"""
    monkeypatch.chdir(os.path.join(test_data_dir, 'intradependencies'))
    # neutralize git checkout so we're really testing the HEAD commit
    output_dir = os.path.join(testing_workdir, 'output')
    execute.compute_builds('.', 'config-name',
                           folders=['zlib', 'uses_zlib'],
                           matrix_base_dir=os.path.join(test_data_dir, 'linux-config-test'),
                           output_dir=output_dir)
    assert os.path.isdir(output_dir)
    files = os.listdir(output_dir)
    assert 'plan.yml' in files
    with open(os.path.join(output_dir, 'plan.yml')) as f:
        plan = yaml.safe_load(f)

    uses_zlib_job = [job for job in plan['jobs'] if job['name'] == 'uses_zlib-1.0-on-centos5-64'][0]
    assert any(task.get('passed') == ['zlib_wannabe-1.2.8-on-centos5-64']
               for task in uses_zlib_job['plan'])


def test_python_build_matrix_expansion(monkeypatch):
    monkeypatch.chdir(test_data_dir)
    tasks = execute.collect_tasks('.', matrix_base_dir=os.path.join(test_data_dir, 'linux-config-test'),
                                  folders=['python_test'])
    assert len(tasks.nodes()) == 2
    assert 'frank-1.0-python_2.7-on-centos5-64' in tasks.nodes()
    assert 'frank-1.0-python_3.6-on-centos5-64' in tasks.nodes()


def test_subpackage_matrix_no_subpackages(monkeypatch):
    """Subpackages should not constitute new entries in the build graph.  They should be lumped in
    with their parent recipe.  However, we have to include them initially for the sake of
    dependency ordering.  Thus we initially include them as though they were full packages, but
    then we squish them together and re-assign and dependency edges."""
    monkeypatch.chdir(test_data_dir)
    tasks = execute.collect_tasks('.', matrix_base_dir=os.path.join(test_data_dir, 'linux-config-test'),
                                  folders=['has_subpackages', 'depends_on_subpackage'])
    assert len(tasks.nodes()) == 2
    assert 'has_subpackages_toplevel-1.0-on-centos5-64' in tasks.nodes()
    assert 'depends_on_subpackage-1.0-on-centos5-64' in tasks.nodes()
    assert 'has_subpackages_subpackage-1.0-on-centos5-64' not in tasks.nodes()
    # this is the actual dependency
    assert ('depends_on_subpackage-1.0-on-centos5-64', 'has_subpackages_subpackage-1.0-on-centos5-64') not in tasks.edges()
    # this is what we remap it to
    assert ('depends_on_subpackage-1.0-on-centos5-64', 'has_subpackages_toplevel-1.0-on-centos5-64') in tasks.edges()


def test_dependency_with_selector_cross_compile(testing_conda_resolve):
    g = execute.collect_tasks(test_data_dir, ['selector_run', 'functools32-feedstock'],
                              matrix_base_dir=os.path.join(test_data_dir, 'config-win'),
                              variant_config_files=os.path.join(test_data_dir, 'conda_build_config.yaml'))
    assert len(g.nodes()) == 6
    # native edge
    assert ('test_run_deps_with_selector-1.0-python_2.7-on-win-64',
            'functools32_wannabe-3.2.3.2-python_2.7-on-win-64') in g.edges()
    # cross edge
    assert ('test_run_deps_with_selector-1.0-python_2.7-target_win-32-on-win-64',
            'functools32_wannabe-3.2.3.2-python_2.7-target_win-32-on-win-64') in g.edges()


def test_collapse_with_win_matrix_and_subpackages(monkeypatch):
    monkeypatch.chdir(test_data_dir)
    tasks = execute.collect_tasks('.', matrix_base_dir=os.path.join(test_data_dir, 'config-win'),
                                  folders=['win_split_outputs_compiler_reduction'])
    # 8 subpackages, but 4 total builds - 2 subpackages per build
    assert len(tasks.nodes()) == 4
    assert 'postgresql-split-10.1-c_compiler_vs2008-on-win-64' in tasks.nodes()
    assert 'postgresql-split-10.1-c_compiler_vs2015-on-win-64' in tasks.nodes()
    assert 'postgresql-split-10.1-c_compiler_vs2008-target_win-32-on-win-64' in tasks.nodes()
    assert 'postgresql-split-10.1-c_compiler_vs2015-target_win-32-on-win-64' in tasks.nodes()


def test_collapse_noarch_python():
    path = os.path.join(test_data_dir, 'noarch_python_recipes')
    folders = ['pkg_a', 'pkg_b']
    variant_file = os.path.join(test_data_dir, 'noarch_python_recipes', 'conda_build_config.yaml')
    tasks = execute.collect_tasks(path, folders, matrix_base_dir=test_config_dir,
                                  variant_config_files=variant_file)
    # 9 nodes,
    # * 1 for the noarch: python pkg_a build on centos5-64
    # * 2 for the noarch: python pkg_a tests on osx-109 and win-32
    # * 6 for pkg_b (3 platforms x 2 python version)
    print(tasks.nodes())
    assert len(tasks.nodes()) == 9
    assert 'pkg_a-1.0.0-on-centos5-64' in tasks.nodes()
    assert 'test-pkg_a-1.0.0-on-osx-109' in tasks.nodes()
    assert 'test-pkg_a-1.0.0-on-win-32' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_3.6-on-osx-109' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_2.7-on-osx-109' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_3.6-on-win-32' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_2.7-on-win-32' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_3.6-on-centos5-64' in tasks.nodes()
    assert 'pkg_b-1.0.0-python_2.7-on-centos5-64' in tasks.nodes()
    # test nodes should be labeled as such
    assert tasks.nodes['test-pkg_a-1.0.0-on-osx-109']['test_only'] == True
    assert tasks.nodes['test-pkg_a-1.0.0-on-win-32']['test_only'] == True
    # 8 edges
    # * 6 pkg_b nodes have an edge to the pkg_a build node
    # * 2 pkg_a tests nodes with edges to the pkg_a build_node
    print(tasks.edges())
    assert len(tasks.edges()) == 8
    a_build_node = 'pkg_a-1.0.0-on-centos5-64'
    assert ('test-pkg_a-1.0.0-on-osx-109', a_build_node) in tasks.edges()
    assert ('test-pkg_a-1.0.0-on-win-32', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_3.6-on-osx-109', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_2.7-on-osx-109', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_3.6-on-win-32', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_2.7-on-win-32', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_3.6-on-centos5-64', a_build_node) in tasks.edges()
    assert ('pkg_b-1.0.0-python_2.7-on-centos5-64', a_build_node) in tasks.edges()
