import pytest
from kravel.kube import KubernetesClient, KubernetesAPIError


@pytest.mark.parametrize("previous,message", [(True, 'Kubernetes API 400: previous terminated container "fluentd" in pod "example" not found'), (False, 'Kubernetes API 400: container "fluentd" in pod "example" is waiting to start: trying and failing to pull image')])
def test_expected_log_unavailability_does_not_raise_an_application_exception(previous, message):
    kube = object.__new__(KubernetesClient)
    def request(*_args, **_kwargs): raise KubernetesAPIError(message)
    kube.request = request
    result = kube.pod_logs("example", "kravel-demo", "fluentd", previous)
    assert result["available"] is False and result["logs"] == "" and "Events" in result["reason"]


def test_permission_denial_is_not_disguised_as_no_logs():
    kube = object.__new__(KubernetesClient)
    def request(*_args, **_kwargs): raise KubernetesAPIError("Kubernetes API 403: forbidden")
    kube.request = request
    with pytest.raises(KubernetesAPIError, match="forbidden"): kube.pod_logs("example", "kravel-demo")
