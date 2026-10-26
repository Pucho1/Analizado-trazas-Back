// CI/CD for the Observa backend: quality checks, analysis, Docker image and Railway deploy.
// The agent only needs Docker, Node (for the Snyk and Railway CLIs) and the SonarScanner tool.
// Jenkins credentials: GitCredentials, dockerHub_credentials, Snik_credentials, RAILWAY_TOKEN
// (a Railway *project* token for the production environment).
pipeline {
    agent any

    environment {
        REPO_URL = 'https://github.com/Pucho1/Analizado-trazas-Back.git'
        SCANNER_SONAR = tool 'SonarScanner'
        IMAGE = 'pucho1/observa-backend'
        // Name of the backend service in the Railway project.
        RAILWAY_SERVICE = 'observa-backend'
        // Throwaway PostgreSQL for the integration tests, unique per build.
        CI_NETWORK = "observa-ci-${env.BUILD_NUMBER}"
        CI_POSTGRES = "observa-ci-postgres-${env.BUILD_NUMBER}"
        CI_RUNNER = "observa-ci-runner-${env.BUILD_NUMBER}"
    }

    options {
        disableConcurrentBuilds()
    }

    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', credentialsId: 'GitCredentials', url: REPO_URL
            }
        }

        stage('Quality') {
            steps {
                echo 'Ruff, mypy and pytest (unit + integration against PostgreSQL)...'
                sh '''
                    docker network create "$CI_NETWORK"
                    docker run -d --name "$CI_POSTGRES" --network "$CI_NETWORK" \
                        -e POSTGRES_USER=observa -e POSTGRES_PASSWORD=observa \
                        -e POSTGRES_DB=observa_test postgres:17

                    for i in $(seq 1 30); do
                        docker exec "$CI_POSTGRES" pg_isready -U observa -d observa_test && break
                        sleep 2
                    done

                    # The workspace is copied in (not mounted) so this also works when Jenkins
                    # itself runs inside a container that talks to the host's Docker.
                    docker create --name "$CI_RUNNER" --network "$CI_NETWORK" -w /src \
                        -e UV_PYTHON_DOWNLOADS=never \
                        -e TEST_DATABASE_URL="postgresql+asyncpg://observa:observa@$CI_POSTGRES:5432/observa_test" \
                        python:3.14-slim sh -c '
                            pip install --quiet uv==0.11.8 &&
                            uv sync --frozen &&
                            uv run ruff check &&
                            uv run ruff format --check &&
                            uv run mypy &&
                            uv run pytest -rs
                        '
                    docker cp . "$CI_RUNNER":/src
                    docker start -a "$CI_RUNNER"
                '''
            }
            post {
                always {
                    sh '''
                        docker rm -f "$CI_RUNNER" "$CI_POSTGRES" || true
                        docker network rm "$CI_NETWORK" || true
                    '''
                }
            }
        }

        stage('SonarQube Analysis') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh '''
                        "$SCANNER_SONAR/bin/sonar-scanner" \
                            -Dsonar.projectKey=observa-backend \
                            -Dsonar.projectName=observa-backend \
                            -Dsonar.sources=app \
                            -Dsonar.tests=tests \
                            -Dsonar.python.version=3.14
                    '''
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                sh 'docker build -t "$IMAGE:$BUILD_NUMBER" -t "$IMAGE:latest" .'
            }
        }

        stage('Snyk Test') {
            steps {
                withCredentials([string(credentialsId: 'Snik_credentials', variable: 'SNYK_TOKEN')]) {
                    // Scans the image (Debian packages and Python dependencies) without
                    // failing the pipeline; results also go to the Snyk dashboard.
                    sh '''
                        npx --yes snyk container test "$IMAGE:$BUILD_NUMBER" --file=Dockerfile || true
                        npx --yes snyk container monitor "$IMAGE:$BUILD_NUMBER" --file=Dockerfile || true
                    '''
                }
            }
        }

        stage('Push Docker Image') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerHub_credentials', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        echo "$DOCKER_PASS" | docker login -u "$DOCKER_USER" --password-stdin
                        docker push "$IMAGE:$BUILD_NUMBER"
                        docker push "$IMAGE:latest"
                    '''
                }
            }
        }

       stage('Deploy to Railway') {
            steps {
                withCredentials([string(credentialsId: 'RAILWAY_TOKEN', variable: 'RAILWAY_TOKEN')]) {
                    // The Railway service uses docker.io/pucho1/observa-backend:latest as its
                    // source; a redeploy pulls the image just pushed.
                    sh '''
                        docker run --rm -e RAILWAY_TOKEN \
                            ghcr.io/railwayapp/cli:latest \
                            railway redeploy --service "$RAILWAY_SERVICE" --yes
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker logout || true'
            sh 'docker image rm "$IMAGE:$BUILD_NUMBER" || true'
        }
    }
}
