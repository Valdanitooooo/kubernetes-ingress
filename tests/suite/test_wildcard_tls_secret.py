import requests
import pytest
from settings import TEST_DATA
from suite.fixtures import PublicEndpoint
from suite.ssl_utils import get_server_certificate_subject
from suite.resources_utils import create_items_from_yaml, delete_items_from_yaml, create_secret_from_yaml, delete_secret
from suite.resources_utils import get_ingress_host_from_yaml, create_common_app, delete_common_app, is_secret_present
from suite.resources_utils import wait_until_all_pods_are_containers_ready, create_ingress_controller
from suite.resources_utils import delete_ingress_controller, replace_secret, wait_before_test, ensure_connection_to_public_endpoint

paths = ["backend1", "backend2"]


class WildcardTLSSecretSetup:
    def __init__(self, public_endpoint: PublicEndpoint, namespace, ingress_host):
        self.public_endpoint = public_endpoint
        self.namespace = namespace
        self.ingress_host = ingress_host


class IngressControllerWithSecret:
    def __init__(self, secret_name):
        self.secret_name = secret_name


@pytest.fixture(scope="class", params=["standard", "mergeable"])
def wildcard_tls_secret_setup(request, kube_apis, ingress_controller_endpoint, test_namespace) -> WildcardTLSSecretSetup:
    ing_type = request.param
    print("------------------------- Deploy Wildcard-Tls-Secret-Example -----------------------------------")
    create_items_from_yaml(kube_apis.extensions_v1_beta1,
                           f"{TEST_DATA}/wildcard-tls-secret/{ing_type}/wildcard-secret-ingress.yaml", test_namespace)
    host = get_ingress_host_from_yaml(f"{TEST_DATA}/wildcard-tls-secret/{ing_type}/wildcard-secret-ingress.yaml")
    common_app = create_common_app(kube_apis.v1, kube_apis.extensions_v1_beta1, test_namespace)
    wait_until_all_pods_are_containers_ready(kube_apis.v1, test_namespace)

    def fin():
        print("Clean up Wildcard-Tls-Secret-Example:")
        delete_items_from_yaml(kube_apis.extensions_v1_beta1,
                               f"{TEST_DATA}/wildcard-tls-secret/{ing_type}/wildcard-secret-ingress.yaml",
                               test_namespace)
        delete_common_app(kube_apis.v1, kube_apis.extensions_v1_beta1, common_app, test_namespace)

    request.addfinalizer(fin)

    return WildcardTLSSecretSetup(ingress_controller_endpoint, test_namespace, host)


@pytest.fixture(scope="class")
def wildcard_tls_secret_ingress_controller(cli_arguments, kube_apis, ingress_controller_prerequisites,
                                           wildcard_tls_secret_setup, request) -> IngressControllerWithSecret:
    """
    Create a Wildcard Ingress Controller according to the installation type
    :param cli_arguments: pytest context
    :param kube_apis: client apis
    :param ingress_controller_prerequisites
    :param wildcard_tls_secret_setup: test-class prerequisites
    :param request: pytest fixture
    :return: IngressController object
    """
    namespace = ingress_controller_prerequisites.namespace
    print("------------------------- Create IC and wildcard secret -----------------------------------")
    secret_name = create_secret_from_yaml(kube_apis.v1, namespace,
                                          f"{TEST_DATA}/wildcard-tls-secret/wildcard-tls-secret.yaml")
    extra_args = [f"-wildcard-tls-secret={namespace}/{secret_name}"]
    name = create_ingress_controller(kube_apis.v1, kube_apis.extensions_v1_beta1, cli_arguments, namespace, extra_args)
    ensure_connection_to_public_endpoint(wildcard_tls_secret_setup.public_endpoint.public_ip,
                                         wildcard_tls_secret_setup.public_endpoint.port,
                                         wildcard_tls_secret_setup.public_endpoint.port_ssl)

    def fin():
        print("Remove IC and wildcard secret:")
        delete_ingress_controller(kube_apis.extensions_v1_beta1, name, cli_arguments['deployment-type'], namespace)
        if is_secret_present(kube_apis.v1, secret_name, namespace):
            delete_secret(kube_apis.v1, secret_name, namespace)

    request.addfinalizer(fin)
    return IngressControllerWithSecret(secret_name)


class TestTLSWildcardSecrets:
    @pytest.mark.parametrize("path", paths)
    def test_response_code_200(self, wildcard_tls_secret_ingress_controller, wildcard_tls_secret_setup, path):
        req_url = f"https://{wildcard_tls_secret_setup.public_endpoint.public_ip}:{wildcard_tls_secret_setup.public_endpoint.port_ssl}/{path}"
        resp = requests.get(req_url, headers={"host": wildcard_tls_secret_setup.ingress_host}, verify=False)
        assert resp.status_code == 200

    def test_certificate_subject(self, wildcard_tls_secret_ingress_controller, wildcard_tls_secret_setup):
        subject_dict = get_server_certificate_subject(wildcard_tls_secret_setup.public_endpoint.public_ip,
                                                      wildcard_tls_secret_setup.ingress_host,
                                                      wildcard_tls_secret_setup.public_endpoint.port_ssl)
        assert subject_dict[b'C'] == b'ES'
        assert subject_dict[b'ST'] == b'CanaryIslands'
        assert subject_dict[b'O'] == b'nginx'
        assert subject_dict[b'OU'] == b'example.com'
        assert subject_dict[b'CN'] == b'example.com'

    def test_certificate_subject_remains_with_invalid_secret(self, kube_apis, ingress_controller_prerequisites, wildcard_tls_secret_ingress_controller,
                                                             wildcard_tls_secret_setup):
        replace_secret(kube_apis.v1, wildcard_tls_secret_ingress_controller.secret_name,
                       ingress_controller_prerequisites.namespace,
                       f"{TEST_DATA}/wildcard-tls-secret/invalid-wildcard-tls-secret.yaml")
        wait_before_test(1)
        subject_dict = get_server_certificate_subject(wildcard_tls_secret_setup.public_endpoint.public_ip,
                                                      wildcard_tls_secret_setup.ingress_host,
                                                      wildcard_tls_secret_setup.public_endpoint.port_ssl)
        assert subject_dict[b'C'] == b'ES'
        assert subject_dict[b'ST'] == b'CanaryIslands'
        assert subject_dict[b'CN'] == b'example.com'

    def test_certificate_subject_updates_after_secret_update(self, kube_apis, ingress_controller_prerequisites, wildcard_tls_secret_ingress_controller,
                                                             wildcard_tls_secret_setup):
        replace_secret(kube_apis.v1, wildcard_tls_secret_ingress_controller.secret_name,
                       ingress_controller_prerequisites.namespace,
                       f"{TEST_DATA}/wildcard-tls-secret/gb-wildcard-tls-secret.yaml")
        wait_before_test(1)
        subject_dict = get_server_certificate_subject(wildcard_tls_secret_setup.public_endpoint.public_ip,
                                                      wildcard_tls_secret_setup.ingress_host,
                                                      wildcard_tls_secret_setup.public_endpoint.port_ssl)
        assert subject_dict[b'C'] == b'GB'
        assert subject_dict[b'ST'] == b'Cambridgeshire'
        assert subject_dict[b'CN'] == b'cafe.example.com'

    def test_response_and_subject_remains_after_secret_delete(self, kube_apis, ingress_controller_prerequisites, wildcard_tls_secret_ingress_controller,
                                                              wildcard_tls_secret_setup):
        delete_secret(kube_apis.v1, wildcard_tls_secret_ingress_controller.secret_name,
                      ingress_controller_prerequisites.namespace)
        wait_before_test(1)
        req_url = f"https://{wildcard_tls_secret_setup.public_endpoint.public_ip}:{wildcard_tls_secret_setup.public_endpoint.port_ssl}/backend1"
        resp = requests.get(req_url, headers={"host": wildcard_tls_secret_setup.ingress_host}, verify=False)
        assert resp.status_code == 200
        subject_dict = get_server_certificate_subject(wildcard_tls_secret_setup.public_endpoint.public_ip,
                                                      wildcard_tls_secret_setup.ingress_host,
                                                      wildcard_tls_secret_setup.public_endpoint.port_ssl)
        assert subject_dict[b'C'] == b'GB'
        assert subject_dict[b'ST'] == b'Cambridgeshire'
        assert subject_dict[b'CN'] == b'cafe.example.com'
