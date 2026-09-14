#include "service.hpp"

int test_service() {
    return sample::Service{}.run(1) == 2 ? 0 : 1;
}

int main() {
    return test_service();
}
