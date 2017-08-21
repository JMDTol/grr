#!/usr/bin/env python
"""Tests for grr.server.flows.general.collectors.

These tests cover the interaction of artifacts. They test that collection of
good artifacts can still succeed if some bad artifacts are defined, and the
various ways of loading artifacts.
"""

import os


from grr import config
from grr.lib import flags
from grr.lib import rdfvalue
from grr.lib import utils
from grr.lib.rdfvalues import paths as rdf_paths
from grr.server import artifact
from grr.server import artifact_registry
from grr.server import data_store
from grr.server.flows.general import collectors
from grr.server.flows.general import transfer
from grr.test_lib import action_mocks
from grr.test_lib import flow_test_lib
from grr.test_lib import test_lib
from grr.test_lib import vfs_test_lib


class TestArtifactCollectorsInteractions(flow_test_lib.FlowTestsBaseclass):
  """Test the collection of artifacts.

  This class loads both real and test artifacts to test the interaction of badly
  defined artifacts with real artifacts.
  """

  def setUp(self):
    super(TestArtifactCollectorsInteractions, self).setUp()
    test_artifacts_file = os.path.join(config.CONFIG["Test.data_dir"],
                                       "artifacts", "test_artifacts.json")
    artifact_registry.REGISTRY.AddFileSource(test_artifacts_file)

  def testNewArtifactLoaded(self):
    """Simulate a new artifact being loaded into the store via the UI."""
    cmd_artifact = """name: "TestCmdArtifact"
doc: "Test command artifact for dpkg."
sources:
- type: "COMMAND"
  attributes:
    cmd: "/usr/bin/dpkg"
    args: ["--list"]
labels: [ "Software" ]
supported_os: [ "Linux" ]
"""
    no_datastore_artifact = """name: "NotInDatastore"
doc: "Test command artifact for dpkg."
sources:
- type: "COMMAND"
  attributes:
    cmd: "/usr/bin/dpkg"
    args: ["--list"]
labels: [ "Software" ]
supported_os: [ "Linux" ]
"""
    test_registry = artifact_registry.ArtifactRegistry()
    test_registry.ClearRegistry()
    test_registry._dirty = False
    with utils.Stubber(artifact_registry, "REGISTRY", test_registry):
      collect_flow = collectors.ArtifactCollectorFlow(None, token=self.token)
      with self.assertRaises(artifact_registry.ArtifactNotRegisteredError):
        artifact_registry.REGISTRY.GetArtifact("TestCmdArtifact")

      with self.assertRaises(artifact_registry.ArtifactNotRegisteredError):
        artifact_registry.REGISTRY.GetArtifact("NotInDatastore")

      # Add artifact to datastore but not registry
      artifact_coll = artifact_registry.ArtifactCollection(
          rdfvalue.RDFURN("aff4:/artifact_store"), token=self.token)
      with data_store.DB.GetMutationPool(token=self.token) as pool:
        for artifact_val in artifact_registry.REGISTRY.ArtifactsFromYaml(
            cmd_artifact):
          artifact_coll.Add(artifact_val, mutation_pool=pool)

      # Add artifact to registry but not datastore
      for artifact_val in artifact_registry.REGISTRY.ArtifactsFromYaml(
          no_datastore_artifact):
        artifact_registry.REGISTRY.RegisterArtifact(
            artifact_val, source="datastore", overwrite_if_exists=False)

      # This should succeeded because the artifacts will be reloaded from the
      # datastore.
      self.assertTrue(collect_flow._GetArtifactFromName("TestCmdArtifact"))

      # We registered this artifact with datastore source but didn't
      # write it into aff4. This simulates an artifact that was
      # uploaded in the UI then later deleted. We expect it to get
      # cleared when the artifacts are reloaded from the datastore.
      with self.assertRaises(artifact_registry.ArtifactNotRegisteredError):
        artifact_registry.REGISTRY.GetArtifact("NotInDatastore")

  def testProcessCollectedArtifacts(self):
    """Test downloading files from artifacts."""
    self.SetupClients(1, system="Windows", os_version="6.2")

    with vfs_test_lib.VFSOverrider(rdf_paths.PathSpec.PathType.REGISTRY,
                                   vfs_test_lib.FakeRegistryVFSHandler):
      with vfs_test_lib.VFSOverrider(rdf_paths.PathSpec.PathType.OS,
                                     vfs_test_lib.FakeFullVFSHandler):
        self._testProcessCollectedArtifacts()

  def _testProcessCollectedArtifacts(self):
    client_mock = action_mocks.FileFinderClientMock()

    # Get KB initialized
    for _ in flow_test_lib.TestFlowHelper(
        artifact.KnowledgeBaseInitializationFlow.__name__,
        client_mock,
        client_id=self.client_id,
        token=self.token):
      pass

    artifact_list = ["WindowsPersistenceMechanismFiles"]
    with test_lib.Instrument(transfer.MultiGetFile,
                             "Start") as getfile_instrument:
      for _ in flow_test_lib.TestFlowHelper(
          collectors.ArtifactCollectorFlow.__name__,
          client_mock,
          artifact_list=artifact_list,
          token=self.token,
          client_id=self.client_id,
          split_output_by_artifact=True):
        pass

      # Check MultiGetFile got called for our runkey files
      # TODO(user): RunKeys for S-1-5-20 are not found because users.sid only
      # expands to users with profiles.
      pathspecs = getfile_instrument.args[0][0].args.pathspecs
      self.assertItemsEqual([x.path
                             for x in pathspecs], [u"C:\\Windows\\TEMP\\A.exe"])

    artifact_list = ["BadPathspecArtifact"]
    with test_lib.Instrument(transfer.MultiGetFile,
                             "Start") as getfile_instrument:
      for _ in flow_test_lib.TestFlowHelper(
          collectors.ArtifactCollectorFlow.__name__,
          client_mock,
          artifact_list=artifact_list,
          token=self.token,
          client_id=self.client_id,
          split_output_by_artifact=True):
        pass

      self.assertFalse(getfile_instrument.args)


def main(argv):
  # Run the full test suite
  test_lib.main(argv)


if __name__ == "__main__":
  flags.StartMain(main)
